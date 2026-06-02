# Support for "neopixel" leds
#
# Copyright (C) 2019-2022  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

BACKGROUND_PRIORITY_CLOCK = 0x7fffffff00000000

BIT_MAX_TIME=.000004
RESET_MIN_TIME=.000050

MAX_MCU_SIZE = 500  # Sanity check on LED chain length

NEOPIXEL_MODE = """
    NP_MODE_OFF           = 0,       // 常灭
    NP_MODE_ON            = 1,       // 常量
    NP_MODE_BREATH        = 2,       // 呼吸灯
    NP_MODE_FLOW          = 3,       // 流动灯
    NP_MODE_RAINBOW       = 4,       // 彩虹灯
    NP_MODE_HAZARD_LIGHTS = 5,       // 双闪
    NP_MODE_COMET         = 6,       // 彗星拖尾
    NP_MODE_CENTER_FLOW   = 7        // 中心扩散/汇聚
"""

class PrinterNeoPixel:
    LED_PRESETS = {
        1: (255, 255, 255, 0, 0, 0),        # CONST: 常亮 (白色)
        2: (180, 255, 255, 0, 5000, 20),    # BREATH: 冰蓝色 S 曲线呼吸 (两端停留长)
        3: (255, 50, 0, 0, 3000, 20),      # FLOW: 橙黄高色差流光 (长渐变)
        4: (255, 255, 255, 0, 6000, 40),    # RAINBOW: 彩虹模式
        5: (255, 165, 0, 0, 1000, 125),     # HAZARD: 橙色警告快闪
        6: (0, 191, 255, 0, 1500, 30),      # COMET: 彗星拖尾 (深天蓝)
        7: (138, 43, 226, 0, 3000, 40),     # CENTER_FLOW: 紫罗兰中心扩散
        0: (0, 0, 0, 0, 0, 0),              # OFF: 关闭
    }
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.mutex = printer.get_reactor().mutex()
        self.name = config.get_name()
        # Configure neopixel
        ppins = printer.lookup_object('pins')
        pin_params = ppins.lookup_pin(config.get('pin'))
        self.mcu = pin_params['chip']
        self.oid = self.mcu.create_oid()
        self.pin = pin_params['pin']
        
        #logging.info(f"PrinterNeoPixel, self.pin ={self.pin}, type:{type(self.pin)}")
        self.mcu.register_config_callback(self.build_config)
        self.neopixel_update_cmd = self.neopixel_send_cmd = None
        # Build color map
        chain_count = config.getint('chain_count', 1, minval=1)
        color_order = config.getlist("color_order", ["GRB"])
        self.use_pwm_dma = config.getint("use_pwm_dma", 1)
        self.color_order = color_order
        if len(color_order) == 1:
            color_order = [color_order[0]] * chain_count
        if len(color_order) != chain_count:
            raise config.error("color_order does not match chain_count")
        color_indexes = []
        for lidx, co in enumerate(color_order):
            if sorted(co) not in (sorted("RGB"), sorted("RGBW")):
                raise config.error("Invalid color_order '%s'" % (co,))
            color_indexes.extend([(lidx, "RGBW".index(c)) for c in co])
        self.color_map = list(enumerate(color_indexes))
        if len(self.color_map) > MAX_MCU_SIZE:
            raise config.error("neopixel chain too long")
        # Initialize color data
        pled = printer.load_object(config, "led")
        self.led_helper = pled.setup_helper(config, self.update_leds,
                                            chain_count)
        self.color_data = bytearray(len(self.color_map))
        self.update_color_data(self.led_helper.get_status()['color_data'])
        self.old_color_data = bytearray([d ^ 1 for d in self.color_data])
        # Register callbacks
        printer.register_event_handler("klippy:connect", self.send_data)
        printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("print_stats_manager:main_stats_changed", self._handle_main_stats_changed)
        
        self.register_gcode()

        self.num_leds = chain_count
        self.stride = len(color_order[0])
        self.print_stats = 'ready'
        self.old_num_leds = 0
        self.auto_mode_enabled = True
        self.neopixel_mode = 2

        self.printer.register_event_handler("ui:sleep", self._handle_ui_event)
        
    def build_config(self):
        #pin = self.mcu.lookup_pin(self.pin)
        bmt = self.mcu.seconds_to_clock(BIT_MAX_TIME)
        rmt = self.mcu.seconds_to_clock(RESET_MIN_TIME)

        self.mcu.add_config_cmd(
            "config_neopixel oid=%d pin=%s data_size=%hu use_pwm_dma=%u "
            " bit_max_ticks=%u reset_min_ticks=%u stride=%u"
            % (self.oid, self.pin, len(self.color_data), int(self.use_pwm_dma), bmt, rmt, self.stride)
        )
        
     

        cmd_queue = self.mcu.alloc_command_queue()
        self.neopixel_update_cmd = self.mcu.lookup_command(
            "neopixel_update oid=%c pos=%hu data=%*s", cq=cmd_queue)
        self.neopixel_send_cmd = self.mcu.lookup_query_command(
            "neopixel_send oid=%c", "neopixel_result oid=%c success=%c",
            oid=self.oid, cq=cmd_queue)
           
        self.neopixel_breath_cmd = self.mcu.lookup_command(
            "neopixel_breath mode=%c red=%c green=%c blue=%c white=%c period=%hu tick_ms=%hu oid=%c"
        )
        self.neopixel_mdoe_cmd = self.mcu.lookup_command(
            "neopixel_mode oid=%c mode=%c red=%c green=%c blue=%c white=%c acitve_led=%c start=%c direction=%c period=%hu tick_ms=%hu"
        )
            
    def cmd_breath_on(self, gcmd):
        # 从gcode参数获取颜色、周期、oid
        r = gcmd.get_int("R", 0)
        g = gcmd.get_int("G", 255)
        b = gcmd.get_int("B", 0)
        w = gcmd.get_int("W", 0)
        period = gcmd.get_int("P", 2000)
        tick_ms = gcmd.get_int("T", 20)
        oid = self.oid
        #if self.color_order == "RGB":
        #    self.neopixel_breath_cmd.send([1, r, g, b, w, period, tick_ms, oid])
        #else:
        #   self.neopixel_breath_cmd.send([1, g, r, b, w, period, tick_ms, oid])
        self.neopixel_breath_cmd.send([1, r, g, b, w, period, tick_ms, oid])

    def cmd_breath_off(self, gcmd):
        oid = self.oid
        self.neopixel_breath_cmd.send([0, 0, 0, 0, 0, 0, 0, self.oid])

    def _get_preset(self, mode, force=False):
        """内部辅助函数：获取预设并解包"""
        if not force and mode == self.neopixel_mode:
            return None
        self.neopixel_mode = mode
        return self.LED_PRESETS.get(mode, self.LED_PRESETS[1])

    def cmd_breath_mode(self, gcmd):
        """G-Code 指令处理: SET_LED_MODE M=..."""
        mode = gcmd.get_int("M", 2) # 默认进入呼吸模式
        preset = self._get_preset(mode, force=True)
        p_r, p_g, p_b, p_w, p_period, p_tick = preset

        # G-Code 参数覆盖 (如果用户没传，则用预设)
        red = gcmd.get_int("RED", p_r)
        green = gcmd.get_int("GREEN", p_g)
        blue = gcmd.get_int("BLUE", p_b)
        white = gcmd.get_int("WHITE", p_w)
        period = gcmd.get_int("PERIOD", p_period)
        tick_ms = gcmd.get_int("TICK", p_tick)
        
        # 辅助参数
        count = gcmd.get_int("COUNT", self.num_leds)
        start = gcmd.get_int("START", 0)
        direction = gcmd.get_int("DIR", 1)

        self._send_led_cmd(mode, red, green, blue, white, count, start, direction, period, tick_ms)
    
    def cmd_breath_enable(self, gcmd):
        """
        G-Code: NEOPIXEL_ENABLE ENABLE=1/0
        1: 开启自动逻辑，并立即根据当前打印机状态刷新灯效
        0: 关闭灯效，并屏蔽所有后续的自动状态切换
        """
        enable = gcmd.get_int("ENABLE", 1)
        if enable:
            if not self.auto_mode_enabled: # 只有从关到开才触发
                self.auto_mode_enabled = True
                # 走标准逻辑，如果当前状态对应的模式和之前记录的一致，则不会发指令
                self._handle_main_stats_changed(None, self.print_stats)
        else:
            self.auto_mode_enabled = False
            self._get_preset(0, force=True) # 记录为关闭状态
            self._send_led_cmd(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    def register_gcode(self):
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("NEOPIXEL_BREATH_ON", self.cmd_breath_on,
            desc="启动neopixel呼吸动画 R= G= B= W= P=（ms）")
        gcode.register_command("NEOPIXEL_BREATH_OFF", self.cmd_breath_off,
            desc="关闭neopixel呼吸动画")
        gcode.register_command("NEOPIXEL_MODE", self.cmd_breath_mode,
            desc=NEOPIXEL_MODE)
        gcode.register_command("NEOPIXEL_ENABLE", self.cmd_breath_enable,
            desc="呼吸灯使能: NEOPIXEL_ENABLE ENABLE=1/0")
            
    def update_color_data(self, led_state):
        color_data = self.color_data
        for cdidx, (lidx, cidx) in self.color_map:
            color_data[cdidx] = int(led_state[lidx][cidx] * 255. + .5)
    def send_data(self, print_time=None):
        old_data, new_data = self.old_color_data, self.color_data
        if new_data == old_data:
            return
        # Find the position of all changed bytes in this framebuffer
        diffs = [[i, 1] for i, (n, o) in enumerate(zip(new_data, old_data))
                 if n != o]
        # Batch together changes that are close to each other
        for i in range(len(diffs)-2, -1, -1):
            pos, count = diffs[i]
            nextpos, nextcount = diffs[i+1]
            if pos + 5 >= nextpos and nextcount < 16:
                diffs[i][1] = nextcount + (nextpos - pos)
                del diffs[i+1]
        # Transmit changes
        ucmd = self.neopixel_update_cmd.send
        for pos, count in diffs:
            ucmd([self.oid, pos, new_data[pos:pos+count]],
                 reqclock=BACKGROUND_PRIORITY_CLOCK)
        old_data[:] = new_data
        # Instruct mcu to update the LEDs
        minclock = 0
        if print_time is not None:
            minclock = self.mcu.print_time_to_clock(print_time)
        scmd = self.neopixel_send_cmd.send
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        for i in range(8):
            params = scmd([self.oid], minclock=minclock,
                          reqclock=BACKGROUND_PRIORITY_CLOCK)
            if params['success']:
                break
        else:
            logging.info("Neopixel update did not succeed")

    def _handle_ready(self):
        """系统就绪后的默认灯效"""
        # 使用 Mode 2 (冰蓝呼吸)
        preset = self._get_preset(2, force=True)
        if self.auto_mode_enabled:
            self._send_led_cmd(2, *preset[:4], self.num_leds, 0, 0, *preset[4:])
        else:
            self._send_led_cmd(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    
    def _handle_main_stats_changed(self, old_status, new_status, force = False):
        """根据打印机状态自动切换灯效"""
        self.print_stats = new_status
        if not self.auto_mode_enabled:
            return

        status_map = {
            'print_start': (3, self.num_leds, 1),           # 开始打印
            'printing':    (1, self._get_progress_count(), 0), # 打印中
            'pausing':     (2, self._get_progress_count(), 0), # 暂停
        }
        m, cnt, dr = status_map.get(new_status, (2, self.num_leds, 0))
        p = self._get_preset(m, force)
        if not force and p is None and cnt == self.old_num_leds:
            return
        if p is None:
            p = self.LED_PRESETS.get(m, self.LED_PRESETS[1])
        self.old_num_leds = cnt
        self._send_led_cmd(m, *p[:4], cnt, 0, dr, *p[4:])

    def _handle_ui_event(self, event_data):
        # 1. 获取基础状态用于判断和日志
        status_macro = self.printer.lookup_object('gcode_macro SMART_STATUS')
        mode = event_data.get('mode', getattr(status_macro, 'led_mode', 1))
        screen = event_data.get('screen', getattr(status_macro, 'ui_screen_active', 1))
        
        print_stats = self.printer.lookup_object('print_stats')
        printer_state = print_stats.state if print_stats else "idle"
        
        # 初始动作描述
        action_desc = ""
        gcode = self.printer.lookup_object('gcode')
        # --- 规则拦截层 ---
        
        # 拦截 1：手动关闭使能
        if not self.auto_mode_enabled:
            action_desc = "SKIP: Auto-mode Disabled"
            # 如果需要记录，可以在此处直接 respond 后返回
            gcode.respond_info(f"[{self.name}] {action_desc}")
            return

        # 拦截 2：打印中逻辑（规则：打印期间强制亮起，不响应 UI 熄屏）
        if printer_state == 'printing':
            action_desc = "SKIP: Printing (Keep Alive)"
            gcode.respond_info(f"[{self.name}] {action_desc}")
            return

        # 拦截 3：手动模式（规则：手动模式下不响应 UI 熄屏/唤醒跳变）
        if mode == 0: 
            action_desc = "SKIP: Manual Mode"
            gcode.respond_info(f"[{self.name}] {action_desc}")
            return

        # --- 执行决策层 ---
        
        if screen == 0:
            self._get_preset(0, force=True) # 强制设为 0 模式，确保状态同步
            self._send_led_cmd(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            action_desc = "LED OFF (Smart Energy Saving)"
        else:
            self._handle_main_stats_changed(None, self.print_stats)
            action_desc = "LED ON (UI Wake-up)"

        # 记录埋点信息
        gcode.respond_info(
            f"[{self.name}] UI_EVENT: "
            f"Mode={'Smart' if mode else 'Manual'}, "
            f"Screen={'Wake' if screen else 'Sleep'}, "
            f"State={printer_state} -> {action_desc}"
        )

    def _get_progress_count(self):
        """
        根据进度条计算亮起灯珠
        """
        try:
            display_status_progress = self.printer.lookup_object('display_status').progress
            progress = display_status_progress if display_status_progress is not None else 0
            return max(1, int(progress * self.num_leds))
        except:
            return self.num_leds # 出错则全亮  

    def _send_led_cmd(self, mode, r, g, b, w, count, start, dire, period, tick):
        # 严格按照 lookup_command 的顺序组织参数
        # 注意：acitve_led 是按照你之前的模板拼写
        params = [
            self.oid & 0xFF,
            mode & 0xFF,
            r & 0xFF, g & 0xFF, b & 0xFF, w & 0xFF,
            count & 0xFF,
            start & 0xFF,
            dire & 0xFF,
            period & 0xFFFF,
            tick & 0xFFFF
        ]
        self.neopixel_mdoe_cmd.send(params)  

    def update_leds(self, led_state, print_time):
        def reactor_bgfunc(eventtime):
            with self.mutex:
                self.update_color_data(led_state)
                self.send_data(print_time)
        self.printer.get_reactor().register_callback(reactor_bgfunc)
    def get_status(self, eventtime=None):
        state = self.led_helper.get_status(eventtime)

        # 打印状态下，且想要改变灯珠数
        if self.auto_mode_enabled and self.print_stats == 'printing':
            current_count = self._get_progress_count()
            state['current_count'] = current_count
            if self.old_num_leds != current_count:
                self.old_num_leds = current_count
                # 发送指令
                self._send_led_cmd(1, 255, 255, 255, 0, current_count, 0, 0, 0, 0)
        return state

def load_config_prefix(config):
    return PrinterNeoPixel(config)
