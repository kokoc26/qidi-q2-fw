import logging

from . import output_pin

class BaseSmartOutputPin:
    def __init__(self, config):
        self.printer =  config.get_printer() 
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()
        self.short_name = self.name.split()[-1]
        
        self.is_enabled = 0
        self.is_active = False
        self.outputpin = None
        self.save_variables = None
        self.toolhead = None
        # 事件回调
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("print_stats:printer_stats_changed", self._handle_printer_stats_changed)
        # 使能开启gcode
        self.gcode.register_mux_command(
            "ENABLE_SMART_PIN",       # 所有实例共用的命令名
            "PIN",                 # G-code 中用来区分的参数 key
            self.short_name,       # 当前实例的 ID
            self.cmd_ENABLE_SMART_PIN, # 调用的回调方法
            desc=self.cmd_ENABLE_SMART_PIN_help
        )
        # 查询状态可以用printer.objects.query接口查询该模块实现

    def _handle_ready(self):
        self.outputpin = self.printer.lookup_object('output_pin ' + self.short_name)
        self.save_variables = self.printer.lookup_object('save_variables')
        self.toolhead = self.printer.lookup_object('toolhead')
        self.execute_logic()
    
    def _handle_printer_stats_changed(self, old_status, new_status):
        pass

    def execute_logic(self):
        logging.info(f"该智能输出引脚:{self.short_name}, 没有特殊实现")

    def get_status(self, eventtime):
        # 获取底层引脚状态
        pin_status = {}
        if self.outputpin:
            pin_status = self.outputpin.get_status(eventtime)
        
        # 构造并返回完整状态快照，不修改实例变量
        return {
            "enable": self.is_enabled, # 建议从模块内部变量获取
            "active": self.is_active,
            **pin_status               # 使用 Python 解构合并字典
        }
    cmd_ENABLE_SMART_PIN_help = "使能/关闭使能智能指针"
    def cmd_ENABLE_SMART_PIN(self, gcmd):
        self.is_enabled = gcmd.get_int('STATUS', 1)
        logging.info(f"SmartPin {self.short_name} set to {self.is_enabled}")
        if not self.is_enabled:
            # 关闭使能时自动关闭
            self.toolhead.register_lookahead_callback(lambda print_time: self.outputpin._set_pin(print_time, 0))
    
class PolarCoolerSmartOutputPin(BaseSmartOutputPin):
    def execute_logic(self):
        logging.info("加载了空调的智能管理")
        self.is_enabled = self.save_variables.allVariables.get('enable_polar_cooler', self.is_enabled)
        self.temp_threshold = 140
        self.last_temp_state = False
        # 暂不启用温度自动管理
        # self.printer.get_reactor().register_timer(self._temp_check_timer, self.printer.get_reactor().NOW)

    def _handle_printer_stats_changed(self, old_status, new_status):
        # 开启逻辑：打印中 或 从暂停恢复
        if new_status == 'printing' and self.is_enabled:
            self.is_active = True
            self.toolhead.register_lookahead_callback(
                lambda print_time: self.outputpin._set_pin(print_time, 1))
        
        # 关闭逻辑：进入 待机、错误、完成 状态
        elif new_status in ['standby', 'error', 'complete', 'cancelled']:
            self.is_active = False
            self.toolhead.register_lookahead_callback(
                lambda print_time: self.outputpin._set_pin(print_time, 0))
        
        # 可选：暂停时关闭 (3.2 目标)
        elif new_status == 'paused':
            self.is_active = False
            self.toolhead.register_lookahead_callback(
                lambda print_time: self.outputpin._set_pin(print_time, 0))
    def cmd_ENABLE_SMART_PIN(self, gcmd):
        super().cmd_ENABLE_SMART_PIN(gcmd)
        self.gcode.run_script_from_command(f"SAVE_VARIABLE VARIABLE=enable_polar_cooler VALUE={self.is_enabled}")
    # 暂不启用温度自动管理
    def _temp_check_timer(self, eventtime):
        try:
            extruder = self.printer.lookup_object('extruder')
            current_temp = extruder.get_status(eventtime)['temperature']
            if current_temp > self.temp_threshold and not self.last_temp_state:
                self.last_temp_state = True
                # self.toolhead.register_lookahead_callback(
                #     lambda print_time: self.outputpin._set_pin(print_time, 1))
            elif current_temp < self.temp_threshold - 2.0 and self.last_temp_state:
                self.last_temp_state = False
                # self.toolhead.register_lookahead_callback(
                #     lambda print_time: self.outputpin._set_pin(print_time, 0))
        except Exception as e:
            pass
        return eventtime + 2.0
        
class BeeperSmartOutputPin(BaseSmartOutputPin):
    def execute_logic(self):
        logging.info("加载了蜂鸣器的智能管理")
        self.is_enabled = self.save_variables.allVariables.get('beep', self.is_enabled)
    
    def cmd_ENABLE_SMART_PIN(self, gcmd):
        super().cmd_ENABLE_SMART_PIN(gcmd)
        self.gcode.run_script_from_command(f"SAVE_VARIABLE VARIABLE=beep VALUE={self.is_enabled}")

class CaselightOutputPin(BaseSmartOutputPin):
    def __init__(self, config):
        super().__init__(config)
        self.printer.register_event_handler("ui:sleep", self._handle_ui_event)
    def execute_logic(self):
        logging.info("加载了灯箱光的智能管理")
        self.is_enabled = 1
    def cmd_ENABLE_SMART_PIN(self, gcmd):
        super().cmd_ENABLE_SMART_PIN(gcmd)
        self.gcode.run_script_from_command(f"SAVE_VARIABLE VARIABLE=enable_caselight VALUE={self.is_enabled}")

    def _handle_ui_event(self, event_data):
        # 1. 获取事件输入参数
        mode = event_data.get('mode', 1)           # 0:手动, 1:智能
        if mode == 0:
            return
        screen = event_data.get('screen', 1)       # 0:熄屏, 1:亮屏
        timelapse = event_data.get('timelapse', 1) # 0:关闭, 1:开启
        ai_enabled = self._check_ai_enable()
        print_stats = self.printer.lookup_object('print_stats')
        printer_state = print_stats.state if print_stats else "idle"

        target_light_on = False

        if screen == 1:
            if self.is_enabled:
                target_light_on = True
            else:
                target_light_on = False
        else:
            if self.is_enabled:
                if printer_state == "printing":
                    # 规则：正在打印时，只有满足“AI开启”或“延时摄影开启”才亮灯
                    if ai_enabled == 1 or timelapse == 1:
                        target_light_on = True
                    else:
                        # 规则 4：节省能源且减少光污染
                        target_light_on = False
                else:
                    # 空闲状态随屏幕熄灭
                    target_light_on = False
            else:
                # 手动强制关闭状态
                target_light_on = False

        # 5. 执行物理引脚操作
        if target_light_on:
            self.is_active = True
            self.toolhead.register_lookahead_callback(
                lambda print_time: self.outputpin._set_pin(print_time, 1)) # 打开照明灯
        else:
            self.is_active = False
            self.toolhead.register_lookahead_callback(
                lambda print_time: self.outputpin._set_pin(print_time, 0)) # 关闭照明灯

        # 6. 埋点输出
        self.gcode.respond_info(
            f"[{self.name}] 照明同步: SCREEN={'亮' if screen else '熄'}, "
            f"PRINT={printer_state}, AI={ai_enabled}, TIMELAPSE={timelapse} "
            f"-> LIGHT={'ON' if target_light_on else 'OFF'}"
        )
    
    def _check_ai_enable(self):
        self.enable_noodle_detection = self.save_variables.allVariables.get('enable_noodle_detection', 0)
        self.enable_pre_print_model_check = self.save_variables.allVariables.get('enable_pre_print_model_check', 0)
        return self.enable_noodle_detection or self.enable_pre_print_model_check
    
    def _handle_printer_stats_changed(self, old_status, new_status):
        # 开启逻辑：打印中 或 从暂停恢复
        if not self.is_enabled:
            return
        status_macro = self.printer.lookup_object('gcode_macro SMART_STATUS')
        timelapse = getattr(status_macro, 'timelapse_enable', 1)
        if new_status == 'printing' and (self._check_ai_enable() or timelapse):
            self.is_active = True
            self.toolhead.register_lookahead_callback(
                lambda print_time: self.outputpin._set_pin(print_time, 1))


# 需要单独实现的智能类
SMART_PIN_MAP = {
    "beeper": BeeperSmartOutputPin,
    "caselight": CaselightOutputPin,
    "polar_cooler": PolarCoolerSmartOutputPin
}

def load_config_prefix(config):
    short_name = config.get_name().split()[-1]
    cls = SMART_PIN_MAP.get(short_name, BaseSmartOutputPin)
    return cls(config)