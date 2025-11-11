

class PrinterHeaterFeng:
    def __init__(self, config):
        self.printer = config.get_printer()
        air_pheaters = self.printer.load_object(config, 'heater_air')
        self.heater = air_pheaters.setup_heater(config, 'B')
        self.get_status = self.heater.get_status
        self.stats = self.heater.stats
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("M150", self.cmd_M150)
        gcode.register_command("M151", self.cmd_M151)
        gcode.register_command("M152", self.cmd_M152)
    def cmd_M150(self, gcmd):
        gcmd.respond_info("send M150")
        temp = gcmd.get_float('S', 0.)
        air_pheaters = self.printer.lookup_object('heater_air')
        air_pheaters.set_temperature(self.heater, temp)
    def cmd_M151(self, gcmd):
        gcmd.respond_info("send M151")
        self.cmd_M150(gcmd)
    def cmd_M152(self, gcmd):
        temp = gcmd.get_int('E', 0)
        gcmd.respond_info("send M152 E%u" % temp) 
        air_pheaters = self.printer.lookup_object('heater_air')
        air_pheaters.set_air_output(self.heater, temp)

def load_config(config):
    return PrinterHeaterFeng(config)