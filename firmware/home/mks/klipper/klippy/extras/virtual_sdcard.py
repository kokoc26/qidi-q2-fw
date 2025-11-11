# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io, glob

VALID_GCODE_EXTS = ['gcode', 'g', 'gco']

DEFAULT_ERROR_GCODE = """
{% if 'heaters' in printer %}
   TURN_OFF_HEATERS
{% endif %}
"""

class VirtualSD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:shutdown",
                                            self.handle_shutdown)
        # sdcard state
        sd = config.get('path')
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        self.current_file = None
        self.current_zip = None
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
        # 断连续打标志位
        self.continue_flag = False
        self.has_run_m4050 = False

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
            "SDCARD_CONTINUOUS_PRINT_FILE", self.cmd_SDCARD_CONTINUOUS_PRINT_FILE,
            desc=self.cmd_SDCARD_CONTINUOUS_PRINT_FILE_help)
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
        if self.current_zip is not None:
            self.current_zip.close()
            self.current_zip = None
        self.file_position = self.file_size = 0
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")
    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error("SD write not supported")
    def _reset_file(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
        if self.current_zip is not None:
            self.current_zip.close()
            self.current_zip = None
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
        self.continue_flag = False
        self._reset_file()
        filename = gcmd.get("FILENAME")
        plateindex = gcmd.get("PLATEINDEX",'1')
        with_check = gcmd.get("WITHCHECK",'1')
        
        if with_check == '1':
            self.has_run_m4050 = False
        else:
            self.has_run_m4050 = True


        if filename[0] == '/':
            filename = filename[1:]
        self._load_file(gcmd, filename, check_subdirs=True, plateindex = plateindex)
        self.do_resume()
    cmd_SDCARD_CONTINUOUS_PRINT_FILE_help = "断连后打印上次的文件"
    def cmd_SDCARD_CONTINUOUS_PRINT_FILE(self, gcmd):
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self.continue_flag = True
        gcmd.respond_info("进入恢复现场逻辑")
        # self.print_stats.note_start()
        # 获取所有参数
        e_temp = gcmd.get_float('ET', 210)
        b_temp = gcmd.get_float('BT', 60)
        c_temp = gcmd.get_float('CT', 0)
        fan_speed = gcmd.get_float('FS', 0)
        aux_fan_speed = gcmd.get_float('AFS', 0)
        chamber_fan_speed = gcmd.get_float('CFS', 0)
        absolute_coord = bool(gcmd.get_int('AC', 1))
        absolute_extrude = bool(gcmd.get_int('AE', 1))

        # 位置参数
        base_x = gcmd.get_float('BX', 0)
        base_y = gcmd.get_float('BY', 0)
        base_z = gcmd.get_float('BZ', 0)
        base_e = gcmd.get_float('BE', 0)
        
        last_x = gcmd.get_float('LX', 0)
        last_y = gcmd.get_float('LY', 0)
        last_z = gcmd.get_float('LZ', 0)
        last_e = gcmd.get_float('LE', 0)
        
        home_x = gcmd.get_float('HX', 0)
        home_y = gcmd.get_float('HY', 0)
        home_z = gcmd.get_float('HZ', 0)
        home_e = gcmd.get_float('HE', 0)
        
        speed = gcmd.get_float('SP', 1500)
        speed_factor = gcmd.get_float('SF', 1.0)
        extrude_factor = gcmd.get_float('EF', 1.0)
        # 设置温度
        self.gcode.run_script_from_command(f"M109 S{e_temp}")
        self.gcode.run_script_from_command(f"M140 S{b_temp}")
        self.gcode.run_script_from_command(f"M141 S{c_temp}")
        # 设置风扇速度
        self.gcode.run_script_from_command(f"SET_FAN_SPEED FAN=cooling_fan SPEED={fan_speed}")
        self.gcode.run_script_from_command(f"SET_FAN_SPEED FAN=auxiliary_cooling_fan SPEED={aux_fan_speed}")
        self.gcode.run_script_from_command(f"SET_FAN_SPEED FAN=chamber_circulation_fan SPEED={chamber_fan_speed}")
        # 设置坐标和挤出模式 - 使用标准G-code命令
        try:
            if absolute_coord:
                self.gcode.run_script_from_command("G90")  # 绝对坐标模式
            else:
                self.gcode.run_script_from_command("G91")  # 相对坐标模式
                
            if absolute_extrude:
                self.gcode.run_script_from_command("M82")  # 绝对挤出模式
            else:
                self.gcode.run_script_from_command("M83")  # 相对挤出模式
        except Exception as e:
            gcmd.respond_info(f"坐标模式设置错误: {str(e)}")
        
        # 设置速度因子和挤出因子 - 使用标准G-code命令
        try:
            self.gcode.run_script_from_command(f"M220 S{speed_factor * 100}")  # 速度因子
            self.gcode.run_script_from_command(f"M221 S{extrude_factor * 100}")  # 挤出因子
        except Exception as e:
            gcmd.respond_info(f"因子设置错误: {str(e)}")
        
        # 设置位置信息 - 使用G92命令而不是直接设置内部状态
        try:
            # 合并后的脚本
            # TODO: 多色进退料and加载床网补偿
            profile_name = str(self.printer.lookup_object('save_variables').allVariables.get('profile_name', 'default'))
            reposition_script = f"""
            BED_MESH_PROFILE LOAD={profile_name}
            SET_KINEMATIC_POSITION X={last_x} Y={last_y} Z={last_z}
            G90
            G1 Z{last_z + 1:.3f} F300     
            G28 X Y
            G1 X{last_x:.3f} Y{last_y:.3f} F3000 
            G1 Z{last_z:.3f} F300 
            {"G91" if not absolute_coord else "G90"}  
            RESUME_1 EXTRUDER={e_temp}
            """

            # 一次性执行整个脚本
            self.gcode.run_script_from_command(reposition_script)
            # 设置速度
            gcode_move = self.printer.lookup_object('gcode_move')
            gcode_move.speed = speed
            
            gcmd.respond_info(f"位置设置为 X={last_x} Y={last_y} Z={last_z} E={last_e}")
        except Exception as e:
            gcmd.respond_info(f"位置设置错误: {str(e)}")
        
        # 设置基准位置和归位位置
        try:
            gcode_move = self.printer.lookup_object('gcode_move')
            gcode_move.base_position = [base_x, base_y, base_z, base_e]
            gcode_move.homing_position = [home_x, home_y, home_z, home_e]
        except Exception as e:
            gcmd.respond_info(f"基准位置设置错误: {str(e)}")

        self._reset_file()
        search_pattern = os.path.join("/home/mks/printer_data/.temp", "*.gcode")
        filename = glob.glob(search_pattern)[0]
        self._load_file(gcmd, filename, check_subdirs=True)
        self.file_position = gcmd.get_int("FP", self.file_position)
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
    def _load_file(self, gcmd, filename, check_subdirs=False, plateindex = '1'):
        # files = self.get_file_list(check_subdirs)
        # flist = [f[0] for f in files]
        # files_by_lower = { fname.lower(): fname for fname, fsize in files }

        fname = filename
        ext = os.path.splitext(filename)[-1].lower()
        if ext == '.3mf':
            file_base_name = os.path.splitext(os.path.basename(filename))[0]
            if os.path.splitext(file_base_name)[-1] == '.gcode':
                dest_path = os.path.splitext(os.path.basename(filename))[0]
            else:
                dest_path = os.path.splitext(os.path.basename(filename))[0] + ".gcode"
            fname = dest_path
        else:
            fname = os.path.split(fname)[1]
        try:
            # if fname not in flist:
            #     fname = files_by_lower[fname.lower()]
            # fname = os.path.join(self.sdcard_dirname, fname)
            fname = os.path.join("/home/mks/printer_data/.temp/", fname)
            logging.info("Loading file %s", fname)
            f = io.open(fname, 'r', newline='')
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise gcmd.error("Unable to open file")
        gcmd.respond_raw("File opened:%s Size:%d Platindex:%s" % (filename, fsize, plateindex))
        gcmd.respond_raw("File selected")
        self.current_file = f
        self.file_position = 0
        self.file_size = fsize
        self.print_stats.set_current_file(filename, plateindex)
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
            self.current_file.seek(self.file_position)
        except:
            logging.exception("virtual_sdcard seek")
            self.work_timer = None
            return self.reactor.NEVER
        self.print_stats.note_start()
        
        # 在执行G-code文件前先执行M4050指令
        # TODO:判断是否是正常开始还是断连续打
        if not self.continue_flag:
            try:
                self.cmd_from_sd = True
                if(self.has_run_m4050 == False):
                    self.gcode.run_script("M4050")
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
        if self.plr_enabled:
            file_path = "/home/mks/scripts/plr/plr_record"
            if os.path.exists(file_path):
                os.remove(file_path)
            plr_file = open(file_path, 'w', buffering=1)
        while not self.must_pause_work:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                except:
                    if self.plr_enabled:
                        plr_file.close()
                    logging.exception("virtual_sdcard read")
                    break
                if not data:
                    # End of file
                    self.lines = 0
                    if self.plr_enabled:
                        plr_file.close()
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
                if self.lines % self.save_every_n_lines == 0 and self.plr_enabled:
                    plr_file.seek(0)
                    plr_file.write(str(self.lines))
                    plr_file.truncate()
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
        if self.plr_enabled:
            plr_file.close()
        self.work_timer = None
        self.cmd_from_sd = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
        elif self.current_file is not None:
            self.print_stats.note_pause()
        else:
            self.print_stats.note_complete()
        return self.reactor.NEVER

def load_config(config):
    return VirtualSD(config)