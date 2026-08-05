# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io, glob, json
import zipfile
import datetime
import time
import shutil

VALID_GCODE_EXTS = ['gcode', 'g', 'gco']

DEFAULT_ERROR_GCODE = """
{% if 'heaters' in printer %}
   TURN_OFF_HEATERS
{% endif %}
"""

class ShadowFileWrapper:
    def __init__(self, file_obj, physical_paths, original_filename):
        self.file_obj = file_obj
        # 确保 physical_paths 是个列表，即使只传了一个路径
        self.physical_paths = physical_paths if isinstance(physical_paths, list) else [physical_paths]
        self.name = original_filename 

    def __getattr__(self, name):
        return getattr(self.file_obj, name)

    def close(self):
        """关闭文件句柄并物理删除所有关联的临时影子文件"""
        if not self.file_obj.closed:
            self.file_obj.close()
        
        for path in self.physical_paths:
            if os.path.exists(path):
                try:
                    os.remove(path)
                    # logging.info(f"[virtual_sdcard] Auto-cleaned: {path}")
                except:
                    pass

    def __del__(self):
        self.close()


class VirtualSD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:shutdown",
                                            self.handle_shutdown)
        # sdcard state
        sd = config.get('path')
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        self.current_file = None
        # self.current_zip = None
        self.file_position = self.file_size = 0
        # Print Stat Tracking
        self.print_stats = self.printer.load_object(config, 'print_stats')
        # Work timer
        self.reactor = self.printer.get_reactor()
        self.must_pause_work = self.cmd_from_sd = False
        self.next_file_position = 0
        self.work_timer = None
        # Error handling
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.on_error_gcode = gcode_macro.load_template(
            config, 'on_error_gcode', DEFAULT_ERROR_GCODE)
        # power lose resume
        self.lines = 0
        self.save_every_n_lines = 50
        self.plr_enabled = config.getboolean('plr_enabled', True)
        self.has_run_m4050 = False
        # 盘号
        self.plate_index = "1"
        self.uid = "native"
        self._printer_off_power_resume = None

        self.cache_dir = "/home/qidi/printer_data/.cache/"
        self.cache_tmp_dir = "/home/qidi/printer_data/.temp"
        try:
            if os.path.exists(self.cache_tmp_dir):
                shutil.rmtree(self.cache_tmp_dir)
            os.makedirs(self.cache_tmp_dir)
            logging.info(f"[virtual_sdcard] Cleaned up 3MF temp directory: {self.cache_tmp_dir}")
        except Exception as e:
            logging.error(f"[virtual_sdcard] Failed to cleanup temp dir: {str(e)}")

        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        for cmd in ['M20', 'M21', 'M23', 'M24', 'M25', 'M26', 'M27']:
            self.gcode.register_command(cmd, getattr(self, 'cmd_' + cmd))
        for cmd in ['M28', 'M29', 'M30']:
            self.gcode.register_command(cmd, self.cmd_error)
        self.gcode.register_command(
            "SDCARD_RESET_FILE", self.cmd_SDCARD_RESET_FILE,
            desc=self.cmd_SDCARD_RESET_FILE_help)
        self.gcode.register_command(
            "SDCARD_PRINT_FILE", self.cmd_SDCARD_PRINT_FILE,
            desc=self.cmd_SDCARD_PRINT_FILE_help)
        self.gcode.register_command(
            "SDCARD_POWER_OFF_CONTINUES_PINT_FILE", self.cmd_SDCARD_POWER_OFF_CONTINUES_PINT_FILE,
            desc=self.cmd_SDCARD_POWER_OFF_CONTINUES_PINT_FILE_help)
        self.accel=100
        self.gcode_adap = False
        self.gcode.register_command(
            "SET_OUTWALL_ACCEL", self.cmd_SET_OUTWALL_ACCEL)
        
    def handle_shutdown(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.current_file.seek(readpos)
                data = self.current_file.read(readcount + 128)
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info("Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                         readpos, repr(data[:readcount]),
                         self.file_position, repr(data[readcount:]))
    def stats(self, eventtime):
        if self.work_timer is None:
            return False, ""
        return True, "sd_pos=%d" % (self.file_position,)
    def get_file_list(self, check_subdirs=False):
        if check_subdirs:
            flist = []
            for root, dirs, files in os.walk(
                    self.sdcard_dirname, followlinks=True):
                for name in files:
                    ext = name[name.rfind('.')+1:]
                    if ext not in VALID_GCODE_EXTS:
                        continue
                    full_path = os.path.join(root, name)
                    r_path = full_path[len(self.sdcard_dirname) + 1:]
                    size = os.path.getsize(full_path)
                    flist.append((r_path, size))
            return sorted(flist, key=lambda f: f[0].lower())
        else:
            dname = self.sdcard_dirname
            try:
                filenames = os.listdir(self.sdcard_dirname)
                return [(fname, os.path.getsize(os.path.join(dname, fname)))
                        for fname in sorted(filenames, key=str.lower)
                        if not fname.startswith('.')
                        and os.path.isfile((os.path.join(dname, fname)))]
            except:
                logging.exception("virtual_sdcard get_file_list")
                raise self.gcode.error("Unable to get file list")
    def get_status(self, eventtime):
        return {
            'file_path': self.file_path(),
            'progress': self.progress(),
            'is_active': self.is_active(),
            'file_position': self.file_position,
            'file_size': self.file_size,
            'plate_index': self.plate_index,
            'uid': self.uid,
        }
    def file_path(self):
        if self.current_file:
            return self.current_file.name
        return None
    def progress(self):
        if self.file_size:
            return float(self.file_position) / self.file_size
        else:
            return 0.
    def is_active(self):
        return self.work_timer is not None
    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + .001)
    def do_resume(self):
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        self.must_pause_work = False
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW)
    def do_cancel(self):
        # 先通知加热器中断任何等待循环
        try:
            heaters = self.printer.lookup_object('heaters')
            if hasattr(heaters, 'abort_waits'):
                heaters.abort_waits()
        except Exception:
            logging.exception("virtual_sdcard do_cancel abort_waits")
        # 再通知 gcode 处理循环中断，确保跳出 _process_commands
        try:
            gcode = self.printer.lookup_object('gcode')
            if hasattr(gcode, 'abort_waits'):
                gcode.abort_waits()
        except Exception:
            logging.exception("virtual_sdcard do_cancel gcode abort_waits")
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
            self.lines = 0
            self.print_stats.note_cancel()
        # if self.current_zip is not None:
        #     self.current_zip.close()
        #     self.current_zip = None
        self.file_position = self.file_size = 0
        self.has_run_m4050 = False
        # self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")
    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error("SD write not supported")
    def _reset_file(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
        # if self.current_zip is not None:
        #     self.current_zip.close()
        #     self.current_zip = None
        self.file_position = self.file_size = 0
        self.has_run_m4050 = False
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")
    cmd_SDCARD_RESET_FILE_help = "Clears a loaded SD File. Stops the print "\
        "if necessary"
    def cmd_SDCARD_RESET_FILE(self, gcmd):
        if self.cmd_from_sd:
            raise gcmd.error(
                "SDCARD_RESET_FILE cannot be run from the sdcard")
        self._reset_file()
    cmd_SDCARD_PRINT_FILE_help = "Loads a SD file and starts the print.  May "\
        "include files in subdirectories."
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get("FILENAME")
        self.plate_index = plateindex = gcmd.get("PLATEINDEX",'1')
        self.uid = uid = gcmd.get("UID",'native')
        self.gcode_adap = bool(gcmd.get_int("A", 0))
        if filename[0] == '/':
            filename = filename[1:]
        self._load_file(gcmd, filename, check_subdirs=True, plateindex = plateindex, uid = uid)
        self.do_resume()
    cmd_SDCARD_POWER_OFF_CONTINUES_PINT_FILE_help = '断电续打接口'
    def cmd_SDCARD_POWER_OFF_CONTINUES_PINT_FILE(self, gcmd):
        gcmd.respond_info("打印机恢复状态ing")
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        import base64
        encoded_state = gcmd.get('STATE', '')
        try:
            state_data = json.loads(base64.b64decode(encoded_state).decode())
            p = state_data.get('params', {})
            logging.info(f"接收到的数据: {p}")
        except Exception as e:
            raise gcmd.error(f"解析状态包失败: {str(e)}")

        def _to_pos(val):
            if isinstance(val, list): return (val + [0.0]*4)[:4]
            return [0.0, 0.0, 0.0, 0.0]

        # 预计算位置变量
        base = _to_pos(p.get('base_position'))
        home = _to_pos(p.get('homing_position'))
        last = _to_pos(p.get('gcode_position'))

        # 直接调用修改后的方法
        self._set_printer_state_internal(p, last, base, home)

        plate_index = p.get('plate_index', '1')
        file_pos = int(float(p.get('file_position', 0))) # 双重保险转换

        self.has_run_m4050 = True # 恢复状态的打印，要禁止M4050
        search_pattern = os.path.join("/home/qidi/printer_data/.cache", "*")
        filename = glob.glob(search_pattern)[0]
        self._load_file(gcmd, filename, check_subdirs=True, plateindex=plate_index)
        # self.abort_epoch += 1
        self.file_position = max(file_pos - 4096, 0)
        self.do_resume()

    def cmd_M20(self, gcmd):
        # List SD card
        files = self.get_file_list()
        gcmd.respond_raw("Begin file list")
        for fname, fsize in files:
            gcmd.respond_raw("%s %d" % (fname, fsize))
        gcmd.respond_raw("End file list")
    def cmd_M21(self, gcmd):
        # Initialize SD card
        gcmd.respond_raw("SD card ok")
    def cmd_M23(self, gcmd):
        # Select SD file
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get_raw_command_parameters().strip()
        if filename.startswith('/'):
            filename = filename[1:]
        self._load_file(gcmd, filename)
    
    def cmd_SET_OUTWALL_ACCEL(self, gcmd):
        self.accel = gcmd.get_int("A", self.accel)

    def _set_printer_state_internal(self, p, last, base, home):
        # --- 1. 数据解析 (保持现状) ---
        def _get_float(key, default=0.0):
            try: return float(p.get(key, default))
            except (ValueError, TypeError): return default
        def _get_bool(key, default=True):
            val = p.get(key, default)
            return str(val).lower() in ("true", "1", "yes") if not isinstance(val, bool) else val

        last_pos = (list(last) + [0.0]*4)[:4]
        is_abs_coord = _get_bool('absolute_coord', True)
        is_abs_extrude = _get_bool('absolute_extrude', True)

        # --- 2. 抽离定义类 G-Code (仅 Host 处理) ---
        define_lines = []
        all_objs = p.get('all_objects', [])
        for obj in [o for o in all_objs if isinstance(o, dict)]:
            name = obj.get('name')
            if not name: continue
            cmd = f"EXCLUDE_OBJECT_DEFINE NAME={name}"
            if obj.get('center'):
                c = obj['center']
                cmd += f" CENTER={c[0]},{c[1]}" if isinstance(c, (list, tuple)) else f" CENTER={c}"
            if obj.get('polygon'):
                cmd += f" POLYGON={json.dumps(obj['polygon'], separators=(',', ':'))}"
            define_lines.append(cmd)

        # --- 3. 构造状态与动作类 G-Code (涉及 MCU) ---
        action_lines = []
        # 排除状态
        excluded_objs = p.get('exclude_objects', [])
        action_lines.extend([f"EXCLUDE_OBJECT NAME={name}" for name in excluded_objs])
        
        # 硬件参数
        targets = {
            'ext': _get_float('extruder_target', 0.0),
            'bed': _get_float('heater_bed_target', 0.0),
            'chm': _get_float('chamber_target', 0.0),
            'f1':  _get_float('cooling_fan_speed', 0.0),
            'f2':  _get_float('auxiliary_cooling_fan_speed', 0.0),
            'f3':  _get_float('chamber_circulation_fan_speed', 0.0),
            'sf':  _get_float('speed_factor', 0.016667) * 60,
            'ef':  _get_float('extrude_factor', 1.0)
        }
        action_lines.extend([
            f"M140 S{targets['bed']:.2f}",
            f"M141 S{targets['chm']:.2f}",
            f"M104 S{targets['ext']:.2f}",
            f"M106 S{int(targets['f1'] * 255)}",
            f"M106 P2 S{int(targets['f2'] * 255)}",
            f"M106 P3 S{int(targets['f3'] * 255)}",
            f"M220 S{int(targets['sf'] * 100)}",
            f"M221 S{int(targets['ef'] * 100)}",
            # "G90" if is_abs_coord else "G91",
            # "M82" if is_abs_extrude else "M83"
        ])

        # 物理恢复动作
        # mesh_profile = p.get('bed_mesh_profile', 'default')
        mesh_profile = str(self.printer.lookup_object('save_variables').allVariables.get('profile_name', 'default'))
        # if mesh_profile:
        #     action_lines.append(f"BED_MESH_PROFILE LOAD={mesh_profile}")

        action_lines.extend([
            f"SET_KINEMATIC_POSITION X={last_pos[0]} Y={last_pos[1]} Z={last_pos[2]}",
            "G90",
            f"G1 Z{last_pos[2] + 5.0} F300",
            "M400", 
            "G28 X Y",
            f"BED_MESH_PROFILE LOAD={mesh_profile}" if mesh_profile else "",
            f"SET_GCODE_OFFSET X={home[0]} Y={home[1]} Z={home[2]}",
            f"CLEAR_NOZZLE_PLR HOTEND={targets['ext']:.2f}",
            f"G1 X{last_pos[0]} Y{last_pos[1]} F3000",
            f"M109 S{targets['ext']:.2f}",
            f"G1 Z{last_pos[2]} F300",
            "M82" if is_abs_extrude else "M83",
            f"G92 E{last_pos[3] if is_abs_extrude else 0}",
            "G90" if is_abs_coord else "G91",
            "M400"
        ])

        logging.info("--- PLR: DEFINE LINES ---\n" + "\n".join(define_lines))
        logging.info("--- PLR: ACTION LINES ---\n" + "\n".join(action_lines))

        # --- 4. 封装分步执行逻辑 ---
        def execute_script():
            logging.info("PLR: Starting phased execution")
            
            # 步骤 A: 执行定义 (分块下发，减少 Host 瞬间负载)
            chunk_size = 10
            for i in range(0, len(define_lines), chunk_size):
                chunk = define_lines[i : i + chunk_size]
                self.gcode.run_script_from_command("\n".join(chunk))   
            # 在定义和动作之间加一个 M400，确保 Host 完成对象解析同步
            self.gcode.run_script_from_command("M400")
            
            # 步骤 B: 执行物理恢复动作 (同样分块)
            for i in range(0, len(action_lines), chunk_size):
                chunk = action_lines[i : i + chunk_size]
                self.gcode.run_script_from_command("\n".join(chunk))
            # 同步 Python 对象
            gcode_move = self.printer.lookup_object('gcode_move')
            gcode_move.speed = _get_float('speed', 50.0)
            logging.info("PLR: Phased execution complete")

        self._printer_off_power_resume = execute_script

    def _load_file(self, gcmd, filename, check_subdirs=False, plateindex='1', uid="native"):
        if not os.path.exists(self.cache_tmp_dir):
            os.makedirs(self.cache_tmp_dir)

        full_path = os.path.join(self.cache_dir, os.path.basename(filename))
        ext = os.path.splitext(filename)[-1].lower()
        
        # 预设变量，防止 UnboundLocalError
        f = None
        fsize = 0

        if ext == '.3mf':
            # 1. 状态守卫
            if not self.print_stats.note_extracting():
                # 即使守卫失败，安全起见也调一下 reset，确保不会因为之前的逻辑残留导致死锁
                self.print_stats.reset()
                raise gcmd.error("Cannot load new file while printer is not in standby state.")

            extracted_filepaths = []
            shadow_path = os.path.join(self.cache_tmp_dir, f"shadow_{uid}_plate_{plateindex}.gcode")
            
            try:
                # --- A. 准备阶段 (目录清理) ---
                try:
                    if os.path.exists(self.cache_tmp_dir):
                        for f_old in os.listdir(self.cache_tmp_dir):
                            try: os.remove(os.path.join(self.cache_tmp_dir, f_old))
                            except: pass
                    else:
                        os.makedirs(self.cache_tmp_dir)
                except Exception as e:
                    logging.warning(f"[virtual_sdcard] Cache clear failed: {e}")

                # --- B. 任务解析 ---
                tasks_config = [
                    (f'Metadata/plate_{plateindex}.gcode', shadow_path),
                    ('Metadata/model_settings.config', os.path.join(self.cache_tmp_dir, 'model_settings.config')),
                    ('Metadata/slice_info.config', os.path.join(self.cache_tmp_dir, 'slice_info.config')),
                    (f'Metadata/pick_{plateindex}.png', os.path.join(self.cache_tmp_dir, f'pick_{plateindex}.png')),
                    (f'Metadata/plate_{plateindex}.png', os.path.join(self.cache_tmp_dir, f'plate_{plateindex}.png'))
                ]

                start_time = time.perf_counter()
                with zipfile.ZipFile(full_path, 'r') as z:
                    all_files_in_zip = z.namelist()
                    active_tasks = []
                    total_bytes_to_read = 0
                    for src, dest in tasks_config:
                        if src in all_files_in_zip:
                            info = z.getinfo(src)
                            active_tasks.append((info, dest))
                            total_bytes_to_read += info.file_size
                    
                    if not total_bytes_to_read:
                        raise gcmd.error("3MF file is empty or missing required metadata.")

                    # --- C. 分块解压 (带 CPU 让渡) ---
                    self.print_stats.set_decompression_progress(0.0)
                    gcmd.respond_info("[virtual_sdcard] extraction_progress: 0.0")
                    
                    cumulative_read = 0
                    last_reported_prog = 0.0
                    chunk_size = 1024 * 1024 * 1 

                    for zinfo, dest_path in active_tasks:
                        extracted_filepaths.append(dest_path)
                        with z.open(zinfo, 'r') as source:
                            with open(dest_path, 'wb') as dest:
                                while True:
                                    data = source.read(chunk_size)
                                    if not data: break
                                    dest.write(data)
                                    cumulative_read += len(data)
                                    
                                    # 进度计算
                                    progress = min(cumulative_read / float(total_bytes_to_read), 0.99)
                                    self.print_stats.set_decompression_progress(progress)
                                    if progress >= last_reported_prog + 0.1:
                                        gcmd.respond_info(f"[virtual_sdcard] extraction_progress: {progress:.1f}")
                                        last_reported_prog = progress
                                    
                                    # 让渡 Reactor
                                    self.printer.get_reactor().pause(0.001)

                # --- D. 完成解压 ---
                self.print_stats.set_decompression_progress(1.0)
                gcmd.respond_info("[virtual_sdcard] extraction_progress: 1.0")
                
                fsize = os.path.getsize(shadow_path)
                raw_f = open(shadow_path, 'r', newline='', encoding='utf-8')
                f = ShadowFileWrapper(raw_f, extracted_filepaths, full_path)
                f.seek(0)

            except Exception as e:
                # 出现异常时，尝试清理已解压的文件碎片
                for p in extracted_filepaths:
                    if os.path.exists(p): 
                        try: os.remove(p)
                        except: pass
                self.print_stats.set_decompression_progress(0.0)
                self.print_stats.reset() # 确保回到 Standby/Ready 状态
                logging.exception("3MF file processing failed")
                raise gcmd.error(f"3MF processing error: {str(e)}")
        else:
            # 普通 Gcode 处理
            try:
                f = io.open(full_path, 'r', newline='')
                f.seek(0, os.SEEK_END)
                fsize = f.tell()
                f.seek(0)
            except:
                logging.exception("virtual_sdcard file open")
                raise gcmd.error("Unable to open file")

        # 共通设置阶段
        # 如果走到这里，说明 f 和 fsize 已经准备就绪
        gcmd.respond_raw(f"File opened:{filename} Size:{fsize} Plateindex:{plateindex}")
        gcmd.respond_raw("File selected")
        
        self.current_file = f
        self.file_position = 0
        self.file_size = fsize

        # 获取gcode句柄后，读取前后文件内对比
        CHUNK_SIZE = 64 * 1024
        found_params = {"fuzzy_skin": None, "fuzzy_skin_mode": None}
        offsets = [0, max(0, fsize - CHUNK_SIZE)]
        try:
            start_time = time.perf_counter()
            for offset in offsets:
                self.current_file.seek(offset)
                chunk = self.current_file.read(CHUNK_SIZE)
                if "fuzzy_skin" in chunk:
                    import re
                    f_skin = re.search(r";\s*fuzzy_skin\s*=\s*(\w+)", chunk)
                    f_mode = re.search(r";\s*fuzzy_skin_mode\s*=\s*(\w+)", chunk)
                    if f_skin: found_params["fuzzy_skin"] = f_skin.group(1)
                    if f_mode: found_params["fuzzy_skin_mode"] = f_mode.group(1)
                # 让渡 Reactor
                self.printer.get_reactor().pause(0.001)

            logging.info(f"[virtual_sdcard] Extracted params: {found_params}")
            self.gcode_adap = (found_params["fuzzy_skin"] == 'external' and found_params["fuzzy_skin_mode"] == 'extrusion')
            duration = (time.perf_counter() - start_time) * 1000  # 转换为毫秒 (ms)
            logging.info(f"[virtual_sdcard] virtual_sdcard seek to {self.file_position} took {duration:.3f}")
        except:
            logging.exception("virtual_sdcard seek")
            self.print_stats.reset()
        # set_current_file 会将状态从 Extracting 切换到 Ready/Printing
        # 如果这一步之前报错了，上面的 except 会捕获并 reset 状态
        self.print_stats.set_current_file(filename, plateindex, uid)


    def cmd_M24(self, gcmd):
        # Start/resume SD print
        self.do_resume()
    def cmd_M25(self, gcmd):
        # Pause SD print
        self.do_pause()
    def cmd_M26(self, gcmd):
        # Set SD position
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        pos = gcmd.get_int('S', minval=0)
        self.file_position = pos
    def cmd_M27(self, gcmd):
        # Report SD print status
        if self.current_file is None:
            gcmd.respond_raw("Not SD printing.")
            return
        gcmd.respond_raw("SD printing byte %d/%d"
                         % (self.file_position, self.file_size))
    def get_file_position(self):
        return self.next_file_position
    def set_file_position(self, pos):
        self.next_file_position = pos
    def is_cmd_from_sd(self):
        return self.cmd_from_sd
    # Background work timer
    def work_handler(self, eventtime):
        logging.info("Starting SD card print (position %d)", self.file_position)
        self.reactor.unregister_timer(self.work_timer)
        try:
            start_time = time.perf_counter()
            self.current_file.seek(self.file_position)
            # 计算耗时
            duration = (time.perf_counter() - start_time) * 1000  # 转换为毫秒 (ms)
            logging.info(f"[virtual_sdcard] virtual_sdcard seek to {self.file_position} took {duration:.3f}")
        except:
            logging.exception("virtual_sdcard seek")
            self.work_timer = None
            return self.reactor.NEVER
        
        self.print_stats.note_start()

        # 恢复断电续打场景
        if self._printer_off_power_resume is not None:
            try:
                self._printer_off_power_resume()
                logging.info("Printer state restored from power-off resume data")
            except Exception as e:
                error_message = f"Power-off resume failed: {str(e)}"
                logging.exception(error_message)
                self.work_timer = None
                self.print_stats.note_error(error_message)
                return self.reactor.NEVER
            finally:
                self._printer_off_power_resume = None

        try:
            self.cmd_from_sd = True
            if(self.has_run_m4050 == False):
                # self.gcode.run_script("M4050")
                self.printer.lookup_object('ai_func_manager').m4050()
                self.has_run_m4050 = True
        except self.gcode.error as e:
            error_message = "M4050 execution failed: " + str(e)
            logging.error(error_message)
            try:
                self.gcode.run_script(self.on_error_gcode.render())
            except:
                logging.exception("virtual_sdcard on_error")
            self.work_timer = None
            self.cmd_from_sd = False
            self.print_stats.note_error(error_message)
            return self.reactor.NEVER
        except:
            logging.exception("virtual_sdcard M4050 execution")
            self.work_timer = None
            self.cmd_from_sd = False
            self.print_stats.note_error("M4050 execution failed")
            return self.reactor.NEVER
        finally:
            self.cmd_from_sd = False


        gcode_mutex = self.gcode.get_mutex()
        partial_input = ""
        lines = []
        error_message = None
        # Recreate the file to balance the wear on the eMMC
        # if self.plr_enabled:
        #     file_path = "/home/qidi/scripts/plr/plr_record"
        #     if os.path.exists(file_path):
        #         os.remove(file_path)
        #     plr_file = open(file_path, 'w', buffering=1)
        while not self.must_pause_work:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                except:
                    # if self.plr_enabled:
                    #     plr_file.close()
                    logging.exception("virtual_sdcard read")
                    break
                if not data:
                    # End of file
                    self.lines = 0
                    # if self.plr_enabled:
                    #     plr_file.close()
                    self.current_file.close()
                    self.current_file = None
                    logging.info("Finished SD card print")
                    self.gcode.respond_raw("Done printing file")
                    break
                lines = data.split('\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            # Pause if any other request is pending in the gcode class
            if gcode_mutex.test():
                self.reactor.pause(self.reactor.monotonic() + 0.100)
                continue
            # Dispatch command
            self.cmd_from_sd = True
            line = lines.pop()
            if sys.version_info.major >= 3:
                next_file_position = self.file_position + len(line.encode()) + 1
            else:
                next_file_position = self.file_position + len(line) + 1
            self.next_file_position = next_file_position
            try:
                self.lines += 1
                # if self.lines % self.save_every_n_lines == 0 and self.plr_enabled:
                #     plr_file.seek(0)
                #     plr_file.write(str(self.lines))
                #     plr_file.truncate()
                # if ";TYPE:Outer wall" in line:
                #     self.gcode.run_script("SET_VELOCITY_LIMIT ACCEL=100 ACCEL_TO_DECEL=50")
                if self.gcode_adap:
                    if ";TYPE:Outer wall" in line or "; FEATURE: Outer wall" in line:
                        # 使用 run_script，这样状态切换指令会和后面的 G-code 严格排队执行
                        # self.gcode.run_script(f"SET_EXTRUDER_ENABLE ENABLE=1\nSET_VELOCITY_LIMIT ACCEL={self.accel} ACCEL_TO_DECEL={self.accel/2}")
                        self.gcode.run_script(f"SET_EXTRUDER_ENABLE ENABLE=1")
                    elif ";TYPE:" in line or "; FEATURE:" in line:
                        self.gcode.run_script("SET_EXTRUDER_ENABLE ENABLE=0")
                self.gcode.run_script(line)
            except self.gcode.error as e:
                error_message = str(e)
                try:
                    self.gcode.run_script(self.on_error_gcode.render())
                except:
                    logging.exception("virtual_sdcard on_error")
                break
            except:

                logging.exception("virtual_sdcard dispatch")
                break
            self.cmd_from_sd = False
            self.file_position = self.next_file_position
            # Do we need to skip around?
            if self.next_file_position != next_file_position:
                try:
                    self.current_file.seek(self.file_position)
                except:
                    logging.exception("virtual_sdcard seek")
                    self.work_timer = None
                    return self.reactor.NEVER
                lines = []
                partial_input = ""
        logging.info("Exiting SD card print (position %d)", self.file_position)
        # if self.plr_enabled:
        #     plr_file.close()
        self.work_timer = None
        self.cmd_from_sd = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
        elif self.current_file is not None:
            self.print_stats.note_pause()
        else:
            self.print_stats.note_complete()
            # self._reset_file()
        return self.reactor.NEVER

def load_config(config):
    return VirtualSD(config)