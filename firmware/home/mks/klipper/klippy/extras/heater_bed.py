# Support for a heated bed
#
# Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

class PrinterHeaterBed:
    def __init__(self, config):
        self.printer = config.get_printer()
        pheaters = self.printer.load_object(config, 'heaters')
        self.heater = pheaters.setup_heater(config, 'B')
        self.get_status = self.heater.get_status
        self.stats = self.heater.stats
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("M140", self.cmd_M140)
        gcode.register_command("M190", self.cmd_M190)
#        gcode.register_command("CLOSE_BED_DEBUG", self.cmd_close_bed_debug)
        self.heater_bed_state = 0
#        self.is_heater_bed = 1
    def cmd_M140(self, gcmd, wait=False):
        # Set Bed Temperature
        temp = gcmd.get_float('S', 0.)
        pheaters = self.printer.lookup_object('heaters')
        pheaters.set_temperature(self.heater, temp, wait)
    def cmd_M190(self, gcmd):
        # Set Bed Temperature and Wait
        self.cmd_M140(gcmd, wait=True)
    # def cmd_close_bed_debug(self, gcmd):
    #     is_close = gcmd.get_int('S', 1)
    #     self.is_heater_bed = is_close

def load_config(config):
    return PrinterHeaterBed(config)
