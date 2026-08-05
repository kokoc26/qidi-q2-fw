from . import box_stepper as bs
import configparser
import configfile  
from . import verify_heater
from . import temperature_sensor
from . import box_heater_fan
from . import controller_fan
from . import box_rfid

class Boxconfig:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.box_name = config.get_name().split()[1]
        self.box_num = int(self.box_name.replace('box', ''))

        base_offset = self.box_num * 4

        config.get_printer().add_object(f'box_stepper slot{base_offset}', bs.BoxExtruderStepper(self.build_config(f'slot{base_offset}', 0, 'stepper', config), config, base_offset))
        config.get_printer().add_object(f'box_stepper slot{base_offset + 1}', bs.BoxExtruderStepper(self.build_config(f'slot{base_offset + 1}', 1, 'stepper', config), config, base_offset + 1))
        config.get_printer().add_object(f'box_stepper slot{base_offset + 2}', bs.BoxExtruderStepper(self.build_config(f'slot{base_offset + 2}', 2, 'stepper', config), config, base_offset + 2))
        config.get_printer().add_object(f'box_stepper slot{base_offset + 3}', bs.BoxExtruderStepper(self.build_config(f'slot{base_offset + 3}', 3, 'stepper', config), config, base_offset + 3))
        config.get_printer().add_object(f'heater_generic heater_box{self.box_num + 1}', config.get_printer().load_object(config, 'heaters').setup_heater(self.build_config(f'heater_generic heater_box{self.box_num + 1}', 3, 'heater_generic', config)))
        config.get_printer().add_object(f'temperature_sensor heater_temp_a_box{self.box_num + 1}', temperature_sensor.load_config_prefix(self.build_config(f'temperature_sensor heater_temp_a_box{self.box_num + 1}', 0, 'temperature_sensor', config)))
        config.get_printer().add_object(f'temperature_sensor heater_temp_b_box{self.box_num + 1}', temperature_sensor.load_config_prefix(self.build_config(f'temperature_sensor heater_temp_b_box{self.box_num + 1}', 1, 'temperature_sensor', config)))
        config.get_printer().add_object(f'box_heater_fan heater_fan_a_box{self.box_num + 1}',box_heater_fan.load_config_prefix(self.build_config(f'box_heater_fan heater_fan_a_box{self.box_num + 1}', 0, 'box_heater_fan', config)))
        config.get_printer().add_object(f'box_heater_fan heater_fan_b_box{self.box_num + 1}',box_heater_fan.load_config_prefix(self.build_config(f'box_heater_fan heater_fan_b_box{self.box_num + 1}', 1, 'box_heater_fan', config)))
        config.get_printer().add_object(f'controller_fan board_fan_box{self.box_num + 1}',controller_fan.load_config_prefix(self.build_config(f'controller_fan board_fan_box{self.box_num + 1}', 0, 'controller_fan', config)))
        config.get_printer().add_object(f'box_rfid card_reader_{self.box_num * 2  + 1}',box_rfid.load_config_prefix(self.build_config(f'box_rfid card_reader_{self.box_num * 2  + 1}', 0, 'box_rfid', config)))
        config.get_printer().add_object(f'box_rfid card_reader_{self.box_num * 2  + 2}',box_rfid.load_config_prefix(self.build_config(f'box_rfid card_reader_{self.box_num * 2  + 2}', 1, 'box_rfid', config)))
    
    def get_stepper_atr(self, slot):
        slot_num = int(slot.replace('slot', ''))
        attribute_name = f'box_stepper_{slot_num}'
        return getattr(self, attribute_name, None)
    
    def build_config(self, name, num, module, config):
        fileconfig = configparser.ConfigParser()
        section_name = name

        fileconfig.add_section(section_name)
        if module == 'stepper':
            indexed_keys = [
                "runout_pin",
                "step_pin",
                "dir_pin",
                "enable_pin",
                "white_pin",
                "red_pin",
            ]
            common_keys = [
                "step_pulse_duration",
                "rotation_distance",
                "microsteps",
            ]
            suffix = "_" + str(num)
            for key in indexed_keys:
                cfg_key = key + suffix
                fileconfig.set(section_name, key, config.get(cfg_key))
            for key in common_keys:
                fileconfig.set(section_name, key, config.get(key))
        elif module == 'heater_generic':
            indexed_keys = [
                "heater_pin",
                "sensor_type",
                "i2c_bus",
                "i2c_mcu",
                "i2c_address",
                "control",
                "pid_Kp",
                "pid_Ki",
                "pid_Kd",
                "min_temp",
                "max_temp",
                "target_max_temp",
            ]
            suffix = "_" + str(module)
            for key in indexed_keys:
                cfg_key = key + suffix
                fileconfig.set(section_name, key, config.get(cfg_key))
            
            fileconfig.add_section(f'verify_heater heater_box{self.box_num + 1}')

            verify_heater_keys = [
                "max_error",
                "check_gain_time",
                "is_box_heater",
            ]
            suffix = "_verify_heater"
            for key in verify_heater_keys:
                cfg_key = key + suffix
                fileconfig.set(f'verify_heater heater_box{self.box_num + 1}', key, config.get(cfg_key))
        elif module == 'temperature_sensor':
            indexed_keys = [
                "sensor_type",
                "sensor_pin",
                "min_temp",
                "max_temp",
            ]
            suffix = "_" + str(module) + "_" + str(num)
            for key in indexed_keys:
                cfg_key = key + suffix
                fileconfig.set(section_name, key, config.get(cfg_key))
        elif module == 'box_heater_fan':
            indexed_keys = [
                "pin",
                "heater",
                "heater_temp",
                "stepper",
                "idle_timeout",
            ]
            suffix = "_" + str(module) + "_" + str(num)
            for key in indexed_keys:
                cfg_key = key + suffix
                fileconfig.set(section_name, key, config.get(cfg_key))
        elif module == 'controller_fan':
            indexed_keys = [
                "pin",
                "heater",
                "stepper",
            ]
            suffix = "_" + str(module)
            for key in indexed_keys:
                cfg_key = key + suffix
                fileconfig.set(section_name, key, config.get(cfg_key))
        elif module == 'box_rfid':
            indexed_keys = [
                "cs_pin",
            ]
            suffix = "_" + str(module) + "_" + str(num)
            for key in indexed_keys:
                cfg_key = key + suffix
                fileconfig.set(section_name, key, config.get(cfg_key))
        access_tracking = {}
        fake_cfg = configfile.ConfigWrapper(
            self.printer, fileconfig, access_tracking, section_name
        )
        return fake_cfg

def load_config_prefix(config):
    return Boxconfig(config)

