import logging, requests
from enum import Enum

class AIErrorType(Enum):
    NONE = ""
    FOREIGN = "foreign"      # 异物
    NOODLE = "noodle"            # 炒面/拉丝
class AISensitivity(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

class AiFuncManager:
    def __init__(self, config):
        self.printer =  config.get_printer() 
        self.gcode = self.printer.lookup_object('gcode')

        self.save_variables = None
        # -----------ai使能标志位------------
        self.ai_error_type = AIErrorType.NONE
        self.ai_configs = {
            AIErrorType.FOREIGN: {
                "enabled": 0,
                "count": 0,
                "sensitivity": AISensitivity.MEDIUM.value,
                "sensitivity_key": "",
                "save_key": "enable_pre_print_model_check" # 对应 save_variables 的键名
            },
            AIErrorType.NOODLE: {
                "enabled": 0,
                "count": 0,
                "sensitivity": AISensitivity.MEDIUM.value,
                "sensitivity_key": "noodle_sensitivity_level",
                "save_key": "enable_noodle_detection"
            }
        }
        # -----------ai使能标志位------------

        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("print_stats:printer_stats_changed", self._handle_stats_changed)
        # ai访问gcode
        self.gcode.register_command('SET_AI_DETECT_RESULT', 
                                    self.cmd_SET_AI_DETECT_RESULT,
                                    desc=self.cmd_SET_AI_DETECT_RESULT_help)
        # 使能ai相关检测开关
        self.gcode.register_command('ENABLE_AI_DETECT', 
                                    self.cmd_ENABLE_AI_DETECT,
                                    desc=self.cmd_ENABLE_AI_DETECT_help)
        
        self.gcode.register_command('SET_AI_SENSITIVITY', 
                                    self.cmd_SET_AI_SENSITIVITY,
                                    desc=self.cmd_SET_AI_SENSITIVITY_help)
        

    def _handle_stats_changed(self, old_status, new_status):
        # 当打印完成后清空状态 复位
        if new_status in ['standby', 'error', 'complete', 'cancelled']:
            self.ai_error_type = AIErrorType.NONE
            for key, value in self.ai_configs.items():
                value['count'] = 0
        
    def _handle_ready(self):
        self.save_variables = self.printer.lookup_object('save_variables')
        for err_type, config in self.ai_configs.items():
            s_key = config['save_key']
            v_key = config['sensitivity_key']
            if s_key:
                config['enabled'] = getattr(self.save_variables, s_key, 0)

            if v_key :
                config['sensitivity'] = getattr(self.save_variables, v_key, config['sensitivity'])
            logging.info(f"AI {err_type.name} 初始化: Enabled={config['enabled']}, Sensitivity={config['sensitivity']}")

    cmd_SET_AI_DETECT_RESULT_help='ai设置结果，SET_AI_DETECT_RESULT TYPE=错误类型 STATE=1/0，1表示触发'
    def cmd_SET_AI_DETECT_RESULT(self, gcmd):
        state = gcmd.get_int("STATE", 0)
        type_input = gcmd.get("TYPE", "").upper()

        if not state:
            self.ai_error_type = AIErrorType.NONE
            return

        try:
            new_type = AIErrorType[type_input]
            if self.ai_configs[new_type]['count'] > 0 or not self.ai_configs[new_type]['enabled']:
                return
            self.ai_error_type = new_type
            self.ai_configs[new_type]['count'] += 1
            self._execute_ai_action(new_type)
        except KeyError:
            logging.warning(f"AI Detect: 收到未知错误类型 {type_input}")
            self.ai_error_type = AIErrorType.NONE 
            
        logging.info(f"AI 状态更新: {self.ai_error_type.name} (Value: {self.ai_error_type.value})")

    cmd_ENABLE_AI_DETECT_help='设置ai使能开关，ENABLE_AI_DETECT TYPE=错误类型 STATE=1/0，1表示使能'
    def cmd_ENABLE_AI_DETECT(self, gcmd):
        state = gcmd.get_int("STATE", 0)
        type_input = gcmd.get("TYPE", "").upper()
        try:
            err_type = AIErrorType[type_input]
            config = self.ai_configs[err_type]
            config["enabled"] = state
            self.gcode.run_script_from_command(
                f"SAVE_VARIABLE VARIABLE={config['save_key']} VALUE={state}")
            
            status_str = "开启" if state else "关闭"
            gcmd.respond_info(f"AI {err_type.name} 检测已 {status_str}")
        except KeyError:
            gcmd.respond_info(f"错误: 未知的AI类型 '{type_input}'")
        
    cmd_SET_AI_SENSITIVITY_help = "设置AI灵敏度: SET_AI_SENSITIVITY TYPE=noodle/foreign VALUE=LOW/MEDIUM/HIGH"
    def cmd_SET_AI_SENSITIVITY(self, gcmd):
        type_input = gcmd.get("TYPE", "").upper()
        value_input = gcmd.get("VALUE", "").upper()
        try:
            err_type = AIErrorType[type_input]
            if value_input not in [s.value for s in AISensitivity]:
                gcmd.respond_info(f"灵敏度无效: {value_input}")
                return

            self.ai_configs[err_type]["sensitivity"] = value_input
            save_key = self.ai_configs[err_type]["sensitivity_key"]
            if save_key:
                self.gcode.run_script_from_command(
                    f"SAVE_VARIABLE VARIABLE={save_key} VALUE='\"{value_input}\"'")
            gcmd.respond_info(f"AI {err_type.name} 灵敏度设为: {value_input}")
        except KeyError:
            gcmd.respond_info(f"错误: 未知类型 '{type_input}'")

    def m4050(self):
        # 目前所有机型的4050都要取消
        self.gcode.respond_info("M4050: Pre-print model check is disabled, skipping detection")
        return  
        if not self.ai_configs[AIErrorType.FOREIGN]["enabled"]:
            self.gcode.respond_info("M4050: Pre-print model check is disabled, skipping detection")
            return        
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        reactor = self.printer.get_reactor()
        self.gcode.respond_info("M4050: Moving to inspection point...")
        gcode.run_script_from_command("G28 X Y")
        gcode.run_script_from_command("G1 X125 Y403 F12000")
        
        toolhead.wait_moves()

        # --- 步骤 2: 原地阻塞轮询 (不阻塞 Reactor) ---
        url = "http://localhost:9010/detection_res#/"
        max_wait_time = 20.0
        start_time = reactor.monotonic()
        self.gcode.respond_info("M4050: Detection started...")

        found_error = False
        while reactor.monotonic() - start_time < max_wait_time:
            try:
                # 使用非阻塞方式让出 CPU 权限，允许 Klipper 处理其他事务（温度、通讯）这里 pause 100ms 相当于让渡使用权
                reactor.pause(reactor.monotonic() + 0.8)

                response = requests.get(url, timeout=0.2) # 降低超时，防止卡顿
                if response.status_code == 200:
                    data = response.json()
                    status = data.get("foreign_count", 0)

                    if status > 0:
                        self.gcode.respond_info("M4050: Foreign object detected!")
                        found_error = True
                        if self.ai_configs[AIErrorType.FOREIGN]['count'] > 0:
                            return
                        self.ai_error_type = AIErrorType.FOREIGN
                        self.ai_configs[AIErrorType.FOREIGN]['count'] += 1
                        break
            except Exception as e:
                pass

        if found_error:
            gcode.run_script_from_command("PAUSE")
        else:
            self.gcode.respond_info("M4050: No foreign objects found. Continuing...")

        self.gcode.respond_info("M4050: Foreign object detection over")

    def _execute_ai_action(self, error_type):
        """
        根据不同的错误类型和对应的开关，决定是否执行暂停动作
        """
        # 如果是 炒面 错误
        if error_type == AIErrorType.NOODLE:
            logging.info("AI: 检测到炒面且开关开启，执行暂停")
            self.gcode.run_script_from_command("PAUSE")

        # 如果是 异物 错误
        elif error_type == AIErrorType.FOREIGN:
            pass

    def get_status(self, eventtime):
        current_count = 0
        if self.ai_error_type in self.ai_configs:
            current_count = self.ai_configs[self.ai_error_type]['count']
        return {
            "enable_foreign": {
              "enable": self.ai_configs[AIErrorType.FOREIGN]['enabled'],
              "sensitivity": self.ai_configs[AIErrorType.FOREIGN]['sensitivity']
            },
            "enable_noodle":  {
              "enable": self.ai_configs[AIErrorType.NOODLE]['enabled'],
              "sensitivity": self.ai_configs[AIErrorType.NOODLE]['sensitivity']
            },
            "ai_state": {
                "error_type": self.ai_error_type.value,
                "counts": current_count
            }
        }


def load_config(config):
    return AiFuncManager(config)