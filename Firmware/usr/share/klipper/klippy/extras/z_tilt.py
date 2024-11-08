# Mechanical bed tilt calibration with multiple Z steppers
#
# Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, os
import mathutil
from . import probe
from extras.base_info import base_dir

class ZAdjustHelper:
    def __init__(self, config, z_count):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.z_count = z_count
        self.z_steppers = []
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
    def handle_connect(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        z_steppers = [s for s in kin.get_steppers() if s.is_active_axis('z')]
        if len(z_steppers) != self.z_count:
            err_msg = """{"code":"key346", "msg":"%s z_positions needs exactly %d items", "values":[]}""" % (self.name, len(z_steppers))
            raise self.printer.config_error(err_msg)
        if len(z_steppers) < 2:
            err_msg = """{"code":"key347", "msg":"%s requires multiple z steppers", "values":[]}""" % self.name
            raise self.printer.config_error(err_msg)
        self.z_steppers = z_steppers
    def adjust_steppers(self, adjustments, speed):
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        curpos = toolhead.get_position()
        # Report on movements
        stepstrs = ["%s = %.6f" % (s.get_name(), a)
                    for s, a in zip(self.z_steppers, adjustments)]
        msg = "Making the following Z adjustments:\n%s" % ("\n".join(stepstrs),)
        gcode.respond_info(msg)
        z_tilt = self.printer.lookup_object('z_tilt')
        for s, a in zip(self.z_steppers, adjustments):
            if s.get_name() == "stepper_z":
                z_tilt.stepper_z_adjustment += a
            elif s.get_name() == "stepper_z1":
                z_tilt.stepper_z1_adjustment += a
        gcode.respond_info("stepper_z_adjustment:%s stepper_z1_adjustment:%s"%(z_tilt.stepper_z_adjustment,z_tilt.stepper_z1_adjustment))
        # Disable Z stepper movements
        toolhead.flush_step_generation()
        for s in self.z_steppers:
            s.set_trapq(None)
        # Move each z stepper (sorted from lowest to highest) until they match
        positions = [(-a, s) for a, s in zip(adjustments, self.z_steppers)]
        positions.sort(key=(lambda k: k[0]))
        first_stepper_offset, first_stepper = positions[0]
        z_low = curpos[2] - first_stepper_offset
        for i in range(len(positions)-1):
            stepper_offset, stepper = positions[i]
            next_stepper_offset, next_stepper = positions[i+1]
            toolhead.flush_step_generation()
            stepper.set_trapq(toolhead.get_trapq())
            curpos[2] = z_low + next_stepper_offset
            try:
                toolhead.move(curpos, speed)
                toolhead.set_position(curpos)
            except Exception as err:
                logging.exception("ZAdjustHelper adjust_steppers")
                toolhead.flush_step_generation()
                for s in self.z_steppers:
                    s.set_trapq(toolhead.get_trapq())
                err_msg = """{"code":"key348", "msg":"toolhead.move(%s, %s) ZAdjustHelper adjust_steppers error:%s", "values":[]}""" % (str(curpos), speed, str(err))
                raise gcode.error(err_msg)
        # Z should now be level - do final cleanup
        last_stepper_offset, last_stepper = positions[-1]
        toolhead.flush_step_generation()
        last_stepper.set_trapq(toolhead.get_trapq())
        curpos[2] += first_stepper_offset
        toolhead.set_position(curpos)

class ZAdjustStatus:
    def __init__(self, printer):
        self.applied = False
        printer.register_event_handler("stepper_enable:motor_off",
                                        self._motor_off)
    def check_retry_result(self, retry_result):
        if retry_result == "done":
            self.applied = True
        return retry_result
    def reset(self):
        self.applied = False
    def get_status(self, eventtime):
        return {'applied': self.applied}
    def _motor_off(self, print_time):
        self.reset()

class RetryHelper:
    def __init__(self, config, error_msg_extra = ""):
        self.printer = config.get_printer()
        self.gcode = config.get_printer().lookup_object('gcode')
        self.default_max_retries = config.getint("retries", 0, minval=0)
        self.default_retry_tolerance = \
            config.getfloat("retry_tolerance", 0., above=0.)
        self.value_label = "Probed points range"
        self.error_msg_extra = error_msg_extra
    def start(self, gcmd):
        self.max_retries = gcmd.get_int('RETRIES', self.default_max_retries,
                                        minval=0, maxval=30)
        self.retry_tolerance = gcmd.get_float('RETRY_TOLERANCE',
                                              self.default_retry_tolerance,
                                              minval=0.0, maxval=1.0)
        self.current_retry = 0
        self.previous = None
        self.increasing = 0
    def check_increase(self, error):
        if self.previous and error > self.previous + 0.0000001:
            self.increasing += 1
        elif self.increasing > 0:
            self.increasing -= 1
        self.previous = error
        return self.increasing > 1
    def check_retry(self, z_positions):
        if self.max_retries == 0:
            return
        error = round(max(z_positions) - min(z_positions),6)
        self.gcode.respond_info(
            "Retries: %d/%d %s: %0.6f tolerance: %0.6f" % (
                self.current_retry, self.max_retries, self.value_label,
                error, self.retry_tolerance))
        if self.check_increase(error):
            err_msg = """{"code":"key349", "msg":"Retries aborting: %s is increasing. %s", "values":[]}""" % (self.value_label, self.error_msg_extra)
            raise self.gcode.error(err_msg)
        if error <= self.retry_tolerance:
            toolhead = self.printer.lookup_object('toolhead')
            if toolhead.G29_flag == True:
                self.write_z_adjustment()
            return "done"
        self.current_retry += 1
        if self.current_retry > self.max_retries:
            err_msg = """{"code":"key350", "msg":"Too many retries", "values":[]}"""
            raise self.gcode.error(err_msg)
        return "retry"
    def write_z_adjustment(self):
        import json
        z_tilt = self.printer.lookup_object('z_tilt')
        with open(z_tilt.stepper_adjustment_path, "w") as f:
            f.write(json.dumps({"stepper_z_adjustment": z_tilt.stepper_z_adjustment, "stepper_z1_adjustment": z_tilt.stepper_z1_adjustment}))
            f.flush()

class ZTilt:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.z_positions = config.getlists('z_positions', seps=(',', '\n'),
                                           parser=float, count=2)
        self.retry_helper = RetryHelper(config)
        self.probe_helper = probe.ProbePointsHelper(config, self.probe_finalize)
        self.probe_helper.minimum_points(2)
        self.z_status = ZAdjustStatus(self.printer)
        self.z_helper = ZAdjustHelper(config, len(self.z_positions))
        # Register Z_TILT_ADJUST command
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('Z_TILT_ADJUST', self.cmd_Z_TILT_ADJUST,
                               desc=self.cmd_Z_TILT_ADJUST_help)
        gcode.register_command('ADJUST_STEPPERS', self.cmd_ADJUST_STEPPERS)
        self.stepper_z_adjustment = 0
        self.stepper_z1_adjustment = 0
        self.stepper_adjustment_path = os.path.join(base_dir, "creality/userdata/config/stepper_adjustment.json")
        self.real_zmax_path = os.path.join(base_dir, "creality/userdata/config/real_zmax.json")
    cmd_Z_TILT_ADJUST_help = "Adjust the Z tilt"
    def cmd_Z_TILT_ADJUST(self, gcmd):
        self.stepper_z_adjustment = 0
        self.stepper_z1_adjustment = 0
        # if os.path.exists(self.stepper_adjustment_path):
        #     os.remove(self.stepper_adjustment_path) 
        self.z_status.reset()
        self.retry_helper.start(gcmd)
        self.probe_helper.start_probe(gcmd)
    def cmd_RM_ADJUSTMENT_FILE(self, gcmd):
        if os.path.exists(self.stepper_adjustment_path):
            os.remove(self.stepper_adjustment_path)  
    def cmd_ADJUST_STEPPERS(self, gcmd):
        adjustments = self.get_adjustments()
        speed = self.probe_helper.get_lift_speed()
        if adjustments:
            self.printer.lookup_object('gcode').run_script_from_command("G4 P200")
            self.z_helper.adjust_steppers(adjustments, speed)       
            self.printer.lookup_object('gcode').run_script_from_command("G4 P500")
    def get_adjustments(self):
        import json
        # 默认一个不可能的值
        stepper_z_adjustment = -10
        stepper_z1_adjustment = -10
        if os.path.exists(self.stepper_adjustment_path):
            result = {}
            with open(self.stepper_adjustment_path, "r") as f:
                try:
                    result = json.loads(f.read())
                    stepper_z_adjustment = result.get("stepper_z_adjustment", -10)
                    stepper_z1_adjustment = result.get("stepper_z1_adjustment", -10)
                except Exception as err:
                    logging.exception(err)
        if stepper_z_adjustment != -10 and stepper_z1_adjustment != -10:
            return [stepper_z_adjustment, stepper_z1_adjustment]
        return None

    def probe_finalize(self, offsets, positions):
        # Setup for coordinate descent analysis
        z_offset = offsets[2]
        logging.info("Calculating bed tilt with: %s", positions)
        params = { 'x_adjust': 0., 'y_adjust': 0., 'z_adjust': z_offset }
        # Perform coordinate descent
        def adjusted_height(pos, params):
            x, y, z = pos
            return (z - x*params['x_adjust'] - y*params['y_adjust']
                    - params['z_adjust'])
        def errorfunc(params):
            total_error = 0.
            for pos in positions:
                total_error += adjusted_height(pos, params)**2
            return total_error
        new_params = mathutil.coordinate_descent(
            params.keys(), params, errorfunc)
        # Apply results
        speed = self.probe_helper.get_lift_speed()
        logging.info("Calculated bed tilt parameters: %s", new_params)
        x_adjust = new_params['x_adjust']
        y_adjust = new_params['y_adjust']
        z_adjust = (new_params['z_adjust'] - z_offset
                    - x_adjust * offsets[0] - y_adjust * offsets[1])
        adjustments = [x*x_adjust + y*y_adjust + z_adjust
                       for x, y in self.z_positions]
        self.z_helper.adjust_steppers(adjustments, speed)
        return self.z_status.check_retry_result(
            self.retry_helper.check_retry([p[2] for p in positions]))
    def get_status(self, eventtime):
            return self.z_status.get_status(eventtime)

def load_config(config):
    return ZTilt(config)
