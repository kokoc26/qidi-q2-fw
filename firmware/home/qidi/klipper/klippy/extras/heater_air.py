# Support for a heated air

from . import heater_air_core

def load_config(config):
    return heater_air_core.PrinterAirHeater(config)