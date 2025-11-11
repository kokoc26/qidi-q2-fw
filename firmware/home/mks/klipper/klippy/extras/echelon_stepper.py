import stepper, chelper
from . import force_move

import logging

import json

class DripModeEndSignal(Exception):
    pass

class EchelonStepper:
    def __init__(self, config):
        self.name = stepper_name = config.get_name().split()[-1]
        self.printer = printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.all_mcus = [
            m for n, m in self.printer.lookup_objects(module='mcu')]

        self.can_home = False
        self.rail = stepper.PrinterRail(
                config, need_position_minmax=False, default_position_endstop=0.)
        self.steppers = self.rail.get_steppers()
        self.retract_dist = config.getfloat('retract_dist', 4.)
        self.velocity = config.getfloat('velocity', 5., above=0.)
        self.color_feeder = config.get('color_feeder')
        self.accel = self.homing_accel = config.getfloat('accel', 0., minval=0.)
        self.next_cmd_time = 0.
        self.print_time = 0.
        self.last_flush_time = 0.
        # Setup iterative solver
        ffi_main, ffi_lib = chelper.get_ffi()
        self.trapq = ffi_main.gc(ffi_lib.trapq_alloc(), ffi_lib.trapq_free)
        self.trapq_append = ffi_lib.trapq_append
        self.trapq_finalize_moves = ffi_lib.trapq_finalize_moves
        self.rail.setup_itersolve('cartesian_stepper_alloc', b'x')
        self.rail.set_trapq(self.trapq)
        self.gcode = gcode = self.printer.lookup_object('gcode')

        # self.endstop_state = self._query_endstop()
        # self.endstop_state = "No Busy"
        
        gcode.register_mux_command('ECHELON_SP', "STEPPER", 
                                    stepper_name, self.cmd_ECHELON_SP)
        gcode.register_mux_command('ECHELON_QUERY', "STEPPER", 
                                    stepper_name, self.cmd_ECHELON_STEPPER_QUERY)
        gcode.register_mux_command('ECHELON_STEPPER', "STEPPER", 
                                    stepper_name, self.cmd_ECHELON_STEPPER)
        gcode.register_mux_command('ECHELON_STEPPER_HOMING', "STEPPER",
                                    stepper_name, self.cmd_ECHELON_STEPPER_HOMING)
        gcode.register_mux_command('ECHELON_INVERT', "STEPPER",
                                    stepper_name, self.cmd_ECHELON_ENDSTOP_INVERT)
                                    

    def sync_print_time(self):
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        if self.next_cmd_time > print_time:
            toolhead.dwell(self.next_cmd_time - print_time)
        else:
            self.next_cmd_time = print_time

    def do_enable(self, enable):
        self.sync_print_time()
        stepper_enable = self.printer.lookup_object('stepper_enable')
        if enable:
            for s in self.steppers:
                se = stepper_enable.lookup_enable(s.get_name())
                se.motor_enable(self.next_cmd_time)
        else:
            for s in self.steppers:
                se = stepper_enable.lookup_enable(s.get_name())
                se.motor_disable(self.next_cmd_time)
        self.sync_print_time()

    def do_set_position(self, setpos):
        self.rail.set_position([setpos, 0., 0.])

    def do_move(self, movepos, speed, accel, sync=True, drip_completion=None):
        self.sync_print_time()
        toolhead = self.printer.lookup_object('toolhead')
        self.print_time = toolhead.get_last_move_time()
        cp = self.rail.get_commanded_position()
        dist = movepos - cp
        axis_r, accel_t, cruise_t, cruise_v = force_move.calc_move_time(
            dist, speed, accel)
        self.trapq_append(self.trapq, self.next_cmd_time,
                          accel_t, cruise_t, accel_t,
                          cp, 0., 0., axis_r, 0., 0.,
                          0., cruise_v, accel)
        self.next_cmd_time = self.next_cmd_time + accel_t + cruise_t + accel_t
        reactor = self.printer.get_reactor()
        if drip_completion != None:
            flush_delay = 0.5
            curtime = reactor.monotonic()
            est_print_time = self.printer.lookup_object('mcu').estimated_print_time(curtime)
            try:
                while self.print_time < self.next_cmd_time:
                    if drip_completion.test():
                        if self.print_time != 0.0:
                            self.next_cmd_time = self.print_time + 0.5
                        else:
                            self.next_cmd_time = toolhead.get_last_move_time() + 2.0
                        self.sync_print_time()
                        raise DripModeEndSignal()
                    curtime = reactor.monotonic()
                    est_print_time = self.printer.lookup_object('mcu').estimated_print_time(curtime)
                    wait_time = self.print_time - est_print_time - flush_delay  
                    if wait_time > 0. :
                        drip_completion.wait(curtime + wait_time)
                        continue
                    npt = min(self.print_time + 0.50, self.next_cmd_time)
                    toolhead.note_mcu_movequeue_activity(npt, set_step_gen_time=True)
                    pt_delay = 0.1
                    flush_time = max(self.last_flush_time, self.print_time - pt_delay)
                    self.print_time = max(self.print_time, npt)
                    want_flush_time = max(flush_time, self.print_time - pt_delay)
                    self.rail.generate_steps(npt)
                    self.trapq_finalize_moves(self.trapq, npt, npt + 2)
            except DripModeEndSignal as e: 
                self.trapq_finalize_moves(self.trapq, reactor.NEVER, 0)
        else:
            self.rail.generate_steps(self.next_cmd_time)
            self.trapq_finalize_moves(self.trapq, self.next_cmd_time + 99999.9,
                                  self.next_cmd_time + 99999.9)
            toolhead.note_mcu_movequeue_activity(self.next_cmd_time)
            if sync:
                self.sync_print_time()
    def _query_endstop(self):
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        return self.rail.endstops[0][0].query_endstop(print_time)
    def do_homing_move(self, movepos, speed, accel, triggered, check_trigger):
        self.homing_accel = accel
        pos = [movepos, 0., 0., 0.]
        endstops = self.rail.get_endstops()
        phoming = self.printer.lookup_object('homing')
        phoming.manual_home(self, endstops, pos, speed,
                            triggered, check_trigger)

    def cmd_ECHELON_ENDSTOP_INVERT(self, gcmd):
        self.rail.endstops[0][0]._invert = ~self.rail.endstops[0][0]._invert
        
    def cmd_ECHELON_SP(self, gcmd):
        self.do_set_position(0.)
    def cmd_ECHELON_STEPPER_QUERY(self, gcmd):
        self.gcode.respond_info(f"ENDSTOP: {self._query_endstop()}")
    def cmd_ECHELON_STEPPER(self, gcmd):
        # color_feeder = self.printer.lookup_object(self.color_feeder)
        color_feeder = self.printer.lookup_object('color_feeder')
        color_feeder._current_feed = self.name
        data = {'color_feeder': self.name}
        self.save_to_json(data, "/home/mks/color_feeder.json")
        self.print_time = self.print_time + 5
        # self.next_cmd_time = self.next_cmd_time + 5
        speed = gcmd.get_float('SPEED', self.velocity, above=0.)
        accel = gcmd.get_float('ACCEL', self.accel, minval=0.)
        movepos = gcmd.get_float('MOVE')
        self.do_move(movepos, speed, accel, True)

    cmd_ECHELON_STEPPER_HOMING_help = "Command a stepper homing"
    def cmd_ECHELON_STEPPER_HOMING(self, gcmd):
        # color_feeder = self.printer.lookup_object(self.color_feeder)
        color_feeder = self.printer.lookup_object('color_feeder')
        color_feeder._current_feed = self.name
        data = {'color_feeder': self.name}
        self.save_to_json(data, "/home/mks/color_feeder.json")
        # self.homing_move = True
        speed = gcmd.get_float('SPEED', self.velocity, above=0.)
        accel = gcmd.get_float('ACCEL', self.accel, minval=0.)
        movepos = gcmd.get_float('MOVE')
        retract_move = gcmd.get_int('RETRACT', 0)
        post_dist = gcmd.get_int('PDI', 0)
        self.do_homing_move(movepos, speed, accel,
                            True, 1)
        self.do_set_position(0.)
        if retract_move == 0:
            self.do_move(-self.retract_dist, speed, accel, True)
        elif retract_move == 1:
            self.do_move(post_dist, speed, accel, True)
        elif retract_move == 2:
            self.do_move(post_dist, speed, accel, True)

    def save_to_json(self, data, filename):
        with open(filename, 'w') as json_file:
            json.dump(data, json_file, indent=4)

    # Toolhead wrappers to support homing
    def flush_step_generation(self):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.flush_step_generation()
    def get_position(self):
        return [self.rail.get_commanded_position(), 0., 0., 0.]
    def set_position(self, newpos, homing_axes=()):
        self.do_set_position(newpos[0])
    def get_last_move_time(self):
        self.sync_print_time()
        return self.next_cmd_time
    def dwell(self, delay):
        self.next_cmd_time += max(0., delay)
    def drip_move(self, newpos, speed, drip_completion):
        self.do_move(newpos[0], speed, self.homing_accel, True, drip_completion)
    def get_kinematics(self):
        return self
    def get_steppers(self):
        return self.steppers
    def calc_position(self, stepper_positions):
        return [stepper_positions[self.rail.get_name()], 0., 0.]
    
    def get_status(self, eventtime):
        return {
            'query_endstop': self._query_endstop()
        }

def load_config_prefix(config):
    return EchelonStepper(config)
