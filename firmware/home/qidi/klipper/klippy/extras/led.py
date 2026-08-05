# Support for PWM driven LEDs
#
# Copyright (C) 2019-2022  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, ast
from .display import display
import math
import configparser
error = configparser.Error

# Time between each led template update
RENDER_TIME = 0.500

# Helper code for common LED initialization and control
class LEDHelper:
    def __init__(self, config, update_func, led_count=1):
        self.printer = config.get_printer()
        self.update_func = update_func
        self.led_count = led_count
        self.need_transmit = False
        self._anim_timer = None
        self._anim_params = None  # (动画类型, 参数)
        self.print_stats = self.printer.load_object(config, 'print_stats')
        self.print_stats_manager = self.printer.load_object(config, 'print_stats_manager')
        self.display_status = self.printer.load_object(config, 'display_status')
        # Initial color
        red = config.getfloat('initial_RED', 0., minval=0., maxval=1.)
        green = config.getfloat('initial_GREEN', 0., minval=0., maxval=1.)
        blue = config.getfloat('initial_BLUE', 0., minval=0., maxval=1.)
        white = config.getfloat('initial_WHITE', 0., minval=0., maxval=1.)
        self.led_state = [(red, green, blue, white)] * led_count
        self.process_led_count = 1
        # Register commands
        name = config.get_name().split()[-1]
        gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command("SET_LED", "LED", name, self.cmd_SET_LED,
                                   desc=self.cmd_SET_LED_help)
        gcode.register_mux_command("LED_BREATH_ON", "LED", name, self.cmd_breath_on,
            desc="启动该LED呼吸灯动画。参数：R=红 G=绿 B=蓝 P=周期秒")
        
        # gcode.register_command("TEST_ERROR", self.cmd_test_error,
        #     desc="测试报错状态")

        gcode.register_mux_command("LED_BREATH_OFF", "LED", name, self.cmd_breath_off,
            desc="关闭该LED呼吸灯动画")
        self.printer.register_event_handler("klippy:shutdown",
                                            self._handle_shutdown)


    def _handle_shutdown(self):
        self.stop_breathing()    
            
        # self.start_breathing((255, 255, 255), 2.0)

    def cmd_breath_on(self, gcmd):
        logging.info(f"cmd_breath_on")
        r = gcmd.get_int("R", 0)
        g = gcmd.get_int("G", 255)
        b = gcmd.get_int("B", 0)
        period = gcmd.get_float("P", 2.0)
        # self.start_breathing((r, g, b), period)

    def cmd_test_error(self, gcmd):
        logging.info(f"test_error")
        self.stop_breathing()
        # r = gcmd.get_int("R", 0)
        # g = gcmd.get_int("G", 255)
        # b = gcmd.get_int("B", 0)
        # period = gcmd.get_float("P", 2.0)
        self.start_breathing((255, 0, 0), 2.0)
        raise error("test_error")

    def cmd_breath_off(self, gcmd):
        self.stop_breathing()
                               
    def start_breathing(self, color, period):
        logging.info(f"start_breathing")
        self.stop_breathing()
        self._anim_params = ("breath", color, period, self.printer.get_reactor().monotonic())
        self._anim_timer = self.printer.get_reactor().register_timer(self._breath_tick, self.printer.get_reactor().NOW)

    def stop_breathing(self):
        if self._anim_timer:
            self.printer.get_reactor().unregister_timer(self._anim_timer)
            self._anim_timer = None
            self._anim_params = None

    def _breath_tick(self, eventtime):
        if not self._anim_params or self._anim_params[0] != "breath":
            return None

        # 扩展的深红色到橙色渐变（添加中间颜色）
        warm_colors = [
            (255, 255, 0),    # 深红色 (偏暖)
            (255, 255, 0),   # 红橙色
            (255, 255, 0),   # 橙红色
            (255, 200, 0),   # 红橙色
            (255, 200, 0),   # 橙红色
            (255, 200, 0),   # 偏橙红色
            (255, 150, 0),   # 橙黄色
            (255, 150, 0),  # 橙色偏黄
            (255, 150, 0),   # 橙黄色
            (255, 100, 0),  # 橙色偏黄
            (255, 100, 0),  # 亮橙色
            (255, 50, 0),  # 浅橙色
            (255, 50, 0),  # 更浅的橙色
            (255, 50, 0),  # 浅橙色
            (255, 100, 0),  # 更浅的橙色
            (255, 100, 0),   # 偏橙红色
            (255, 150, 0),   # 橙黄色
            (255, 150, 0),  # 橙色偏黄
            (255, 150, 0),   # 橙黄色
            (255, 200, 0),  # 橙色偏黄
            (255, 200, 0),    # 深红色 (偏暖)
            (255, 200, 0),   # 红橙色
            (255, 255, 0),   # 橙红色
            (255, 255, 0),   # 红橙色
            (255, 255, 0),   # 橙红色
        ]
        
        # 单个LED呼吸效果（用于待机状态）
        def single_breath_bgfunc(print_time):
            _, color, period, t0 = self._anim_params
            t = self.printer.get_reactor().monotonic() - t0
            if self.process_led_count > 0:
                self.process_led_count = 0
            # 正弦波呼吸效果
            brightness = (math.sin(2 * math.pi * t / 5) + 1) / 2
            brightness = brightness ** 0.5 * 0.99 # 平方使效果更平滑
            
            # 将白色为主的呼吸效果
            # warm_brightness = brightness * 0.8  # 稍微降低亮度
            led_state = []
            for i in range(self.led_count):
                r = 255 / 255.0 * brightness
                g = 255 / 255.0 * brightness
                b = 255 / 255.0 * brightness
                led_state.append((r, g, b, 0.0))
            
            self.led_state = led_state
            self.need_transmit = True
            self.check_transmit(print_time)
                
        # 正式打印前准备状态下的颜色变换效果
        def wave_flow_bgfunc(print_time):
                _, color, period, t0 = self._anim_params
                t = self.printer.get_reactor().monotonic() - t0
                if self.process_led_count > 0:
                    self.process_led_count = 0
                # 固定亮度值
                fixed_brightness = 0.8
                
                flow_offset = int((t / 3) * len(warm_colors) * 2) % len(warm_colors)
                
                led_state = []
                for i in range(self.led_count):
                    # 计算每个LED的颜色索引，形成流动效果
                    # 每个LED的颜色索引基于其位置和时间
                    # 改为从左往右：i递增时，color_idx也递增
                    # 为了从左往右流动，我们让最左边的LED使用更小的color_idx
                    # 所以使用 (flow_offset + i) % len(warm_colors)
                    # 24 - i 用这个把原来从右到左
                    color_idx =  (flow_offset + (24 - i)) % len(warm_colors)
                    base_r, base_g, base_b = warm_colors[color_idx]
                    
                    # 应用固定亮度
                    r = base_r / 255.0 * fixed_brightness
                    g = base_g / 255.0 * fixed_brightness
                    b = base_b / 255.0 * fixed_brightness
                    
                    led_state.append((r, g, b, 0.0))
                
                self.led_state = led_state
                self.need_transmit = True
                self.check_transmit(print_time)
        
        # 进度条（打印状态）
        def progress_wave_bgfunc(print_time):
            _, color, period, t0 = self._anim_params
            t = self.printer.get_reactor().monotonic() - t0
            
            # 获取打印进度
            progress = self.display_status.progress if self.display_status.progress is not None else 0
            # lit_leds = int(self.led_count * progress + 0.5)

            if progress == 0:
                lit_leds = 1
            else:
                lit_leds = max(1, int(self.led_count * progress + 0.5))
                lit_leds = min(lit_leds, self.led_count)

            if self.process_led_count == lit_leds:
                return eventtime + 1
            else:
                self.process_led_count = lit_leds

            led_state = []
            for i in range(self.led_count):
                if i < lit_leds:
                    led_state.append((1, 1, 1, 0.0))
                else:
                    led_state.append((0, 0, 0, 0.0))

            self.led_state = led_state
            self.need_transmit = True
            self.check_transmit(print_time)

        # 进度条呼吸（打印状态）
        def progress_breath_bgfunc(print_time):
            _, color, period, t0 = self._anim_params
            t = self.printer.get_reactor().monotonic() - t0
            
            # 获取打印进度
            # progress = self.display_status.progress if self.display_status.progress is not None else 0
            # # lit_leds = int(self.led_count * progress + 0.5)

            # if progress == 0:
            #     lit_leds = 1
            # else:
            #     lit_leds = max(1, int(self.led_count * progress + 0.5))
            #     lit_leds = min(lit_leds, self.led_count)

            # if self.process_led_count == lit_leds:
            #     return eventtime + 1
            # else:
            #     self.process_led_count = lit_leds
            brightness = (math.sin(2 * math.pi * t / 5) + 1) / 2
            brightness = brightness ** 0.5 * 0.99 # 平方使效果更平滑
            led_state = []
            for i in range(self.led_count):
                if i < self.process_led_count:
                    r = 255 / 255.0 * brightness
                    g = 255 / 255.0 * brightness
                    b = 255 / 255.0 * brightness
                    led_state.append((r, g, b, 0.0))
                else:
                    led_state.append((0, 0, 0, 0.0))

            self.led_state = led_state
            self.need_transmit = True
            self.check_transmit(print_time)

        # 根据状态选择动画效果
        if (self.print_stats.state == "printing" and 
            self.print_stats_manager.current_main_status == "print_start" ):
            wave_flow_bgfunc(None)
        elif self.print_stats.state == "printing":
            progress_wave_bgfunc(None)
        elif self.print_stats.state == "paused":
            progress_breath_bgfunc(None)
        else:
            single_breath_bgfunc(None)

        return eventtime + 0.1
        
    def get_led_count(self):
        return self.led_count
        
    def set_color(self, index, color):
        if index is None:
            new_led_state = [color] * self.led_count
            if self.led_state == new_led_state:
                return
        else:
            if self.led_state[index - 1] == color:
                return
            new_led_state = list(self.led_state)
            new_led_state[index - 1] = color
        self.led_state = new_led_state
        self.need_transmit = True
        
    def set_color_range(self, start_index, end_index, color):
        """设置从start_index到end_index范围内的所有LED颜色"""
        if start_index < 1 or end_index > self.led_count or start_index > end_index:
            return
        new_led_state = list(self.led_state)
        for i in range(start_index - 1, end_index):
            new_led_state[i] = color
        self.led_state = new_led_state
        self.need_transmit = True
        
    def check_transmit(self, print_time):
        if not self.need_transmit:
            return
        self.need_transmit = False
        try:
            self.update_func(self.led_state, print_time)
        except self.printer.command_error as e:
            logging.exception("led update transmit error")
            
    cmd_SET_LED_help = "Set the color of an LED"
    def cmd_SET_LED(self, gcmd):
        # Parse parameters
        red = gcmd.get_float('RED', 0., minval=0., maxval=1.)
        green = gcmd.get_float('GREEN', 0., minval=0., maxval=1.)
        blue = gcmd.get_float('BLUE', 0., minval=0., maxval=1.)
        white = gcmd.get_float('WHITE', 0., minval=0., maxval=1.)
        index = gcmd.get_int('INDEX', None, minval=1, maxval=self.led_count)
        range_mode = gcmd.get_int('RANGE', 0, minval=0, maxval=1)  # 新增RANGE参数
        transmit = gcmd.get_int('TRANSMIT', 1)
        sync = gcmd.get_int('SYNC', 1)
        color = (red, green, blue, white)
        
        # Update and transmit data
        def lookahead_bgfunc(print_time):
            if range_mode and index is not None:
                # RANGE模式：设置从1到index的所有LED
                self.set_color_range(1, index, color)
            else:
                # 原有模式
                self.set_color(index, color)
                
            if transmit:
                self.check_transmit(print_time)
                
        if sync:
            # Sync LED Update with print time and send
            toolhead = self.printer.lookup_object('toolhead')
            toolhead.register_lookahead_callback(lookahead_bgfunc)
        else:
            # Send update now (so as not to wake toolhead and reset idle_timeout)
            lookahead_bgfunc(None)
            
    def get_status(self, eventtime=None):
        return {'color_data': self.led_state}

# Main LED tracking code
class PrinterLED:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.led_helpers = {}
        self.active_templates = {}
        self.render_timer = None
        # Load templates
        dtemplates = display.lookup_display_templates(config)
        self.templates = dtemplates.get_display_templates()
        gcode_macro = self.printer.lookup_object("gcode_macro")
        self.create_template_context = gcode_macro.create_template_context
        # Register handlers
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("SET_LED_TEMPLATE", self.cmd_SET_LED_TEMPLATE,
                               desc=self.cmd_SET_LED_TEMPLATE_help)
                               
    def setup_helper(self, config, update_func, led_count=1):
        led_helper = LEDHelper(config, update_func, led_count)
        name = config.get_name().split()[-1]
        self.led_helpers[name] = led_helper
        return led_helper
        
    def _activate_timer(self):
        if self.render_timer is not None or not self.active_templates:
            return
        reactor = self.printer.get_reactor()
        self.render_timer = reactor.register_timer(self._render, reactor.NOW)
        
    def _activate_template(self, led_helper, index, template, lparams):
        key = (led_helper, index)
        if template is not None:
            uid = (template,) + tuple(sorted(lparams.items()))
            self.active_templates[key] = (uid, template, lparams)
            return
        if key in self.active_templates:
            del self.active_templates[key]
            
    def _render(self, eventtime):
        if not self.active_templates:
            # Nothing to do - unregister timer
            reactor = self.printer.get_reactor()
            reactor.register_timer(self.render_timer)
            self.render_timer = None
            return reactor.NEVER
        # Setup gcode_macro template context
        context = self.create_template_context(eventtime)
        def render(name, **kwargs):
            return self.templates[name].render(context, **kwargs)
        context['render'] = render
        # Render all templates
        need_transmit = {}
        rendered = {}
        template_info = self.active_templates.items()
        for (led_helper, index), (uid, template, lparams) in template_info:
            color = rendered.get(uid)
            if color is None:
                try:
                    text = template.render(context, **lparams)
                    parts = [max(0., min(1., float(f)))
                             for f in text.split(',', 4)]
                except Exception as e:
                    logging.exception("led template render error")
                    parts = []
                if len(parts) < 4:
                    parts += [0.] * (4 - len(parts))
                rendered[uid] = color = tuple(parts)
            need_transmit[led_helper] = 1
            led_helper.set_color(index, color)
        context.clear() # Remove circular references for better gc
        # Transmit pending changes
        for led_helper in need_transmit.keys():
            led_helper.check_transmit(None)
        return eventtime + RENDER_TIME
        
    cmd_SET_LED_TEMPLATE_help = "Assign a display_template to an LED"
    def cmd_SET_LED_TEMPLATE(self, gcmd):
        led_name = gcmd.get("LED")
        led_helper = self.led_helpers.get(led_name)
        if led_helper is None:
            raise gcmd.error("Unknown LED '%s'" % (led_name,))
        led_count = led_helper.get_led_count()
        index = gcmd.get_int("INDEX", None, minval=1, maxval=led_count)
        template = None
        lparams = {}
        tpl_name = gcmd.get("TEMPLATE")
        if tpl_name:
            template = self.templates.get(tpl_name)
            if template is None:
                raise gcmd.error("Unknown display_template '%s'" % (tpl_name,))
            tparams = template.get_params()
            for p, v in gcmd.get_command_parameters().items():
                if not p.startswith("PARAM_"):
                    continue
                p = p.lower()
                if p not in tparams:
                    raise gcmd.error("Invalid display_template parameter: %s"
                                     % (p,))
                try:
                    lparams[p] = ast.literal_eval(v)
                except ValueError as e:
                    raise gcmd.error("Unable to parse '%s' as a literal" % (v,))
        if index is not None:
            self._activate_template(led_helper, index, template, lparams)
        else:
            for i in range(led_count):
                self._activate_template(led_helper, i+1, template, lparams)
        self._activate_timer()

PIN_MIN_TIME = 0.100
MAX_SCHEDULE_TIME = 5.0

# Handler for PWM controlled LEDs
class PrinterPWMLED:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        # Configure pwm pins
        ppins = printer.lookup_object('pins')
        cycle_time = config.getfloat('cycle_time', 0.010, above=0.,
                                     maxval=MAX_SCHEDULE_TIME)
        hardware_pwm = config.getboolean('hardware_pwm', False)
        self.pins = []
        for i, name in enumerate(("red", "green", "blue", "white")):
            pin_name = config.get(name + '_pin', None)
            if pin_name is None:
                continue
            mcu_pin = ppins.setup_pin('pwm', pin_name)
            mcu_pin.setup_max_duration(0.)
            mcu_pin.setup_cycle_time(cycle_time, hardware_pwm)
            self.pins.append((i, mcu_pin))
        if not self.pins:
            raise config.error("No LED pin definitions found in '%s'"
                               % (config.get_name(),))
        self.last_print_time = 0.
        # Initialize color data
        pled = printer.load_object(config, "led")
        self.led_helper = pled.setup_helper(config, self.update_leds, 1)
        self.prev_color = color = self.led_helper.get_status()['color_data'][0]
        for idx, mcu_pin in self.pins:
            mcu_pin.setup_start_value(color[idx], 0.)
            
    def update_leds(self, led_state, print_time):
        if print_time is None:
            eventtime = self.printer.get_reactor().monotonic()
            mcu = self.pins[0][1].get_mcu()
            print_time = mcu.estimated_print_time(eventtime) + PIN_MIN_TIME
        print_time = max(print_time, self.last_print_time + PIN_MIN_TIME)
        color = led_state[0]
        for idx, mcu_pin in self.pins:
            if self.prev_color[idx] != color[idx]:
                mcu_pin.set_pwm(print_time, color[idx])
                self.last_print_time = print_time
        self.prev_color = color
        
    def get_status(self, eventtime=None):
        return self.led_helper.get_status(eventtime)

def load_config(config):
    return PrinterLED(config)

def load_config_prefix(config):
    return PrinterPWMLED(config)