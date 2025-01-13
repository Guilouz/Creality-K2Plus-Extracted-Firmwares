# Tracking of PWM controlled heaters and their temperature control
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, logging, threading
import numpy as np
######################################################################
# Heater
######################################################################

KELVIN_TO_CELSIUS = -273.15
MAX_HEAT_TIME = 5.0
AMBIENT_TEMP = 25.
PID_PARAM_BASE = 255.

class Heater:
    def __init__(self, config, sensor):
        self.config = config
        self._info_array = [0]*1
        self.info_array = np.array(self._info_array, dtype=np.int)
        self.info_array_addr_int = self.info_array.ctypes.data
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        # Setup sensor
        self.sensor = sensor
        self.min_temp = config.getfloat('min_temp', minval=KELVIN_TO_CELSIUS)
        self.max_temp = config.getfloat('max_temp', above=self.min_temp)
        self.sensor.setup_minmax(self.min_temp, self.max_temp)
        self.sensor.setup_callback(self.temperature_callback)
        self.pwm_delay = self.sensor.get_report_time_delta()
        # Setup temperature checks
        self.min_extrude_temp = config.getfloat(
            'min_extrude_temp', 170.,
            minval=self.min_temp, maxval=self.max_temp)
        is_fileoutput = (self.printer.get_start_args().get('debugoutput')
                         is not None)
        self.can_extrude = self.min_extrude_temp <= 0. or is_fileoutput
        self.max_power = config.getfloat('max_power', 1., above=0., maxval=1.)
        self.smooth_time = config.getfloat('smooth_time', 1., above=0.)
        self.inv_smooth_time = 1. / self.smooth_time
        self.lock = threading.Lock()
        self.last_temp = self.smoothed_temp = self.target_temp = 0.
        self.last_temp_time = 0.
        # pwm caching
        self.next_pwm_time = 0.
        self.last_pwm_value = 0.
        # Setup control algorithm sub-class
        algos = {'watermark': ControlBangBang, 'pid': ControlPID}
        algo = config.getchoice('control', algos)
        self.control = algo(self, config)
        # Setup output heater pin
        heater_pin = config.get('heater_pin')
        ppins = self.printer.lookup_object('pins')
        self.mcu_pwm = ppins.setup_pin('pwm', heater_pin)
        pwm_cycle_time = config.getfloat('pwm_cycle_time', 0.100, above=0.,
                                         maxval=self.pwm_delay)
        self.mcu_pwm.setup_cycle_time(pwm_cycle_time)
        self.mcu_pwm.setup_max_duration(MAX_HEAT_TIME)
        # Load additional modules
        self.printer.load_object(config, "verify_heater %s" % (self.name,))
        self.printer.load_object(config, "pid_calibrate")
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("SET_HEATER_TEMPERATURE", "HEATER",
                                   self.name, self.cmd_SET_HEATER_TEMPERATURE,
                                   desc=self.cmd_SET_HEATER_TEMPERATURE_help)
        # PTC加热前一分钟内使用50%功率加热，一分钟后可以开启100%功率加热
        self.info_array[0]=self.can_extrude
        self.start_heating_seconds = 0
        if self.name == "chamber_heater":
            self.stop_heating = False
            self.target_temp = 0.
            self.printer.register_event_handler('klippy:ready', self.register_chamber_heater_timer)
    def register_chamber_heater_timer(self):
        reactor = self.printer.get_reactor()
        self._chamber_heater_do_query_timer = reactor.register_timer(self._handle_check_chamber_heater)
        reactor.update_timer(self._chamber_heater_do_query_timer, reactor.NOW)
    def _handle_check_chamber_heater(self, eventtime):
        gcode = self.printer.lookup_object('gcode')
        num = 0
        fan_feedback = None
        if self.config.has_section('fan_feedback'):
            fan_feedback = self.printer.lookup_object('fan_feedback')
            if self.control.heating and self.last_pwm_value > 0 and self.target_temp and fan_feedback.cx_fan_status.get("fan0_speed", 0) == 0:
                for _ in range(12):
                    # 判断连续12s内风扇是否都是处于停止状态
                    self.printer.get_reactor().pause(self.printer.get_reactor().monotonic() + 1.0)
                    if self.control.heating and self.last_pwm_value > 0 and self.target_temp and fan_feedback.cx_fan_status.get("fan0_speed", 0) == 0:
                        num += 1
                    else:
                        break
                if num == 12:
                    self.stop_heating = True
                    gcode._respond_error("""{"code":"key519", "msg":"PTC fan_speed is 0, turn off PTC heaters", "values":[]}""")
                    gcode.run_script_from_command("M141 S0")               
        return eventtime + 1.0

    def set_pwm(self, read_time, value):
        if self.target_temp <= 0.:
            value = 0.
        if ((read_time < self.next_pwm_time or not self.last_pwm_value)
            and abs(value - self.last_pwm_value) < 0.05):
            # No significant change in value - can suppress update
            return
        pwm_time = read_time + self.pwm_delay
        self.next_pwm_time = pwm_time + 0.75 * MAX_HEAT_TIME
        self.last_pwm_value = value
        self.mcu_pwm.set_pwm(pwm_time, value)
        #logging.debug("%s: pwm=%.3f@%.3f (from %.3f@%.3f [%.3f])",
        #              self.name, value, pwm_time,
        #              self.last_temp, self.last_temp_time, self.target_temp)
    def temperature_callback(self, read_time, temp):
        with self.lock:
            time_diff = read_time - self.last_temp_time
            self.last_temp = temp
            self.last_temp_time = read_time
            self.control.temperature_update(read_time, temp, self.target_temp)
            temp_diff = temp - self.smoothed_temp
            adj_time = min(time_diff * self.inv_smooth_time, 1.)
            self.smoothed_temp += temp_diff * adj_time
            self.can_extrude = (self.smoothed_temp >= self.min_extrude_temp)
            self.info_array[0]=self.can_extrude
        #logging.debug("temp: %.3f %f = %f", read_time, temp)

    # External commands
    def get_pwm_delay(self):
        return self.pwm_delay
    def get_max_power(self):
        return self.max_power
    def get_smooth_time(self):
        return self.smooth_time
    def set_temp(self, degrees):
        if self.name == 'extruder' and hasattr(self.control, 'dynamically_modify_pid'):
            self.control.dynamically_modify_pid(degrees)
        if degrees and (degrees < self.min_temp or degrees > self.max_temp):
            raise self.printer.command_error(
                """{"code":"key340", "msg":"Heaters %s Requested temperature (%.1f) out of range (%.1f:%.1f)", "values":["%s", %.1f, %.1f, %.1f]}"""
                % (self.name, degrees, self.min_temp, self.max_temp, self.name, degrees, self.min_temp, self.max_temp))
        with self.lock:
            self.target_temp = degrees
    def get_temp(self, eventtime):
        print_time = self.mcu_pwm.get_mcu().estimated_print_time(eventtime) - 5.
        with self.lock:
            if self.last_temp_time < print_time:
                return 0., self.target_temp
            return self.smoothed_temp, self.target_temp
    def check_busy(self, eventtime):
        with self.lock:
            return self.control.check_busy(
                eventtime, self.smoothed_temp, self.target_temp)
    def set_control(self, control):
        with self.lock:
            old_control = self.control
            self.control = control
            self.target_temp = 0.
        return old_control
    def alter_target(self, target_temp):
        if target_temp:
            target_temp = max(self.min_temp, min(self.max_temp, target_temp))
        self.target_temp = target_temp
    def stats(self, eventtime):
        with self.lock:
            target_temp = self.target_temp
            last_temp = self.last_temp
            last_pwm_value = self.last_pwm_value
        is_active = target_temp or last_temp > 50.
        return is_active, '%s: target=%.0f temp=%.1f pwm=%.3f' % (
            self.name, target_temp, last_temp, last_pwm_value)
    def get_status(self, eventtime):
        with self.lock:
            target_temp = self.target_temp
            smoothed_temp = self.smoothed_temp
            last_pwm_value = self.last_pwm_value
        return {'temperature': round(smoothed_temp, 2), 'target': target_temp,
                'power': last_pwm_value}
    cmd_SET_HEATER_TEMPERATURE_help = "Sets a heater temperature"
    def cmd_SET_HEATER_TEMPERATURE(self, gcmd):
        temp = gcmd.get_float('TARGET', 0.)
        wait = True if gcmd.get_int('WAIT', 0)==1 else False
        if self.name == "chamber_heater":
            self.target_temp = temp
            # 重置停止加热标识
            if self.target_temp > 40:
                self.stop_heating = False 
        pheaters = self.printer.lookup_object('heaters')
        pheaters.set_temperature(self, temp, wait=wait)


######################################################################
# Bang-bang control algo
######################################################################

class ControlBangBang:
    def __init__(self, heater, config):
        self.config = config
        self.printer = config.get_printer()
        self.heater = heater
        self.heater_max_power = heater.get_max_power()
        self.max_delta = config.getfloat('max_delta', 2.0, above=0.)
        self.heating = False
        self.long_temp =False
        self.old_temp = 0.0
        self.cnt_temp = 0
        self.prev_temp = AMBIENT_TEMP
        self.temp_coff = 1.
        self.diff_tempa = 0
        self.diff_tempb = 0
        self.count = 0
    def temperature_update(self, read_time, temp, target_temp):
        if (temp + 5.0) < target_temp:
            self.long_temp = True
            self.old_temp = 0.0
            self.cnt_temp = 0
        if target_temp >= 20 and target_temp<=120:
            if temp + 0.7 > target_temp:
                self.long_temp =False
            if self.long_temp:
                if self.old_temp <= 0.01 or self.old_temp < temp:
                    self.old_temp = temp
                    self.cnt_temp = 0
                    # self.diff_tempa = 16.1 + (119-16.1)/100.*(target_temp-20.0)
                    # self.diff_tempb = 16.3 + (119.5-16.3)/100.*(target_temp-20.0)
                    self.diff_tempa = 16.1 + 1.029 * (target_temp-20.0)
                    self.diff_tempb = 16.3 + 1.032 * (target_temp-20.0)
                elif self.old_temp > temp:
                    self.cnt_temp = self.cnt_temp + 1
                    if self.cnt_temp > 10:
                        self.long_temp =False
            else:
                # self.diff_tempa = 19.1 + (119.7-19.1)/100.*(target_temp-20.0)
                # self.diff_tempb = 19.3 + (120.2-19.3)/100.*(target_temp-20.0)
                self.diff_tempa = 19.1 + 1.006 * (target_temp-20.0)
                self.diff_tempb = 19.3 + 1.009 * (target_temp-20.0)
            if self.heating and temp >= self.diff_tempb:
                self.heating = False
            elif not self.heating and temp <= self.diff_tempa:
                self.heating = True
        else:
            if self.heating and temp >= target_temp:
                self.heating = False
            elif not self.heating and temp <= target_temp-self.max_delta:
                self.heating = True
        fan_speed = 0
        fan_feedback = None
        if self.config.has_section('fan_feedback'):
            fan_feedback = self.printer.lookup_object('fan_feedback')
            fan_speed = fan_feedback.cx_fan_status.get("fan0_speed", 0)
        is_chamber_heater = True if self.heater.name == "chamber_heater" else False
        if self.heating:
            if self.prev_temp > 0.1:
                if self.prev_temp - target_temp > 3.:
                    self.temp_coff = 0.3 * self.temp_coff
                elif self.prev_temp - target_temp > 2.:
                    self.temp_coff = 0.5 *self.temp_coff
                elif self.prev_temp - target_temp > 1.5:
                    self.temp_coff = 0.65 * self.temp_coff
                elif self.prev_temp - target_temp > 1.:
                    self.temp_coff = 0.8 * self.temp_coff
                elif self.prev_temp < target_temp:
                    self.temp_coff = 1.5 * self.temp_coff
            if (temp + 1.5) < target_temp:
                self.temp_coff = 1.0
            if self.temp_coff < 0.3:
                self.temp_coff = 0.3
            elif self.temp_coff > 1.0:
                self.temp_coff = 1.0
            self.prev_temp = 0.
            heater_bed_state = self.printer.lookup_object('heater_bed').heater_bed_state
            heater_bed_last_pwm_value = self.printer.lookup_object('heater_bed').last_pwm_value
            # 在PTC加热的时候处理热床加热逻辑
            if is_chamber_heater and heater_bed_state == 1:
                # 热床加热中,不允许开启PTC加热, PTC加热暂停，优先保证热床加热，热床加热完成，再开始PTC加热
                self.temp_coff = 0
                self.heater.start_heating_seconds = 0
            elif is_chamber_heater and self.temp_coff == 0:
                self.temp_coff = 1.0
            elif is_chamber_heater and temp < target_temp-self.max_delta and self.heater.start_heating_seconds < 200:
                # PTC加热前一分钟内使用50%功率加热 why is 200? REPORT_TIME = 0.300 60/REPORT_TIME=200
                self.heater.start_heating_seconds += 1
                self.temp_coff = 0.5
            # elif is_chamber_heater and heater_bed_last_pwm_value > 0.75 and self.temp_coff > 0.5:
            #     # 如果热床的PID输出功率超过75%，则PTC加热功率减低到50%，直到热床输出功率降低到75%以下
            #     self.temp_coff = 0.5
            # elif is_chamber_heater and self.heater.start_heating_seconds == 200:
            #     # 一分钟后可以开启100%功率加热
            #     if temp < target_temp - 1.5:
            #         self.temp_coff = 1.0      
            # 使用count来计数PTC加热的时间,count=20的时候,大约是连续加热了6s
            # temp_coff == 0 时对count进行重置
            if is_chamber_heater and target_temp and self.count < 20 and self.temp_coff > 0:
                self.count += 1
            elif is_chamber_heater and target_temp and self.temp_coff == 0:
                self.count = 0
            # # 到达目标温度附近时, 加热功率调小到12%
            # if is_chamber_heater and temp > target_temp+self.max_delta:
            #     self.temp_coff = 0
            # elif is_chamber_heater and temp > target_temp:
            #     self.temp_coff = 0.12
            # elif is_chamber_heater and temp > target_temp-self.max_delta:
            #     self.temp_coff = 0.2
            # PTC在加热 但是PTC风扇转速为0时 关闭PTC加热
            if is_chamber_heater and self.heater.stop_heating:
                self.temp_coff = 0
            self.heater.set_pwm(read_time, self.heater_max_power * self.temp_coff)
        else:
            self.heater.set_pwm(read_time, 0.)
            if target_temp > 0.1:
                if self.prev_temp < temp:
                    self.prev_temp = temp
            else:
                self.prev_temp = 0.
                self.temp_coff = 1.0
            if is_chamber_heater:
                self.count = 0
                self.heater.start_heating_seconds = 0 

    def check_busy(self, eventtime, smoothed_temp, target_temp):

        return smoothed_temp < target_temp-self.max_delta


######################################################################
# Proportional Integral Derivative (PID) control algo
######################################################################

PID_SETTLE_DELTA = 2.
PID_SETTLE_SLOPE = .5

class ControlPID:
    def __init__(self, heater, config):
        self.printer = config.get_printer()
        self.config = config
        self.oldco = 0
        self.heater = heater
        self.heater_max_power = heater.get_max_power()
        self.Kp = config.getfloat('pid_Kp') / PID_PARAM_BASE
        self.Ki = config.getfloat('pid_Ki') / PID_PARAM_BASE
        self.Kd = config.getfloat('pid_Kd') / PID_PARAM_BASE
        self.high_temp_value = config.getint('high_temp_value', default=280)
        self.Kp_ht = config.getfloat('pid_Kp_high_temp',default=config.getfloat('pid_Kp')) / PID_PARAM_BASE
        self.Ki_ht = config.getfloat('pid_Ki_high_temp',default=config.getfloat('pid_Ki')) / PID_PARAM_BASE
        self.Kd_ht = config.getfloat('pid_Kd_high_temp',default=config.getfloat('pid_Kd')) / PID_PARAM_BASE
        self.pid_calibrate_Kp = None
        self.pid_calibrate_Ki = None
        self.pid_calibrate_Kd = None
        self.pid_calibrate_Kp_ht = None
        self.pid_calibrate_Ki_ht = None
        self.pid_calibrate_Kd_ht = None
        self.min_deriv_time = heater.get_smooth_time()
        self.temp_integ_max = 0.
        if self.Ki:
            self.temp_integ_max = self.heater_max_power / self.Ki
        self.prev_temp = AMBIENT_TEMP
        self.prev_temp_time = 0.
        self.prev_temp_deriv = 0.
        self.prev_temp_integ = 0.
        self.heating = False
        # state 0未在加热 1加热中 2已达到目标温度
        self.heater_bed_state = 0
    def dynamically_modify_pid(self, target_temp):
        if target_temp > self.high_temp_value:
            if self.pid_calibrate_Kp_ht:
                self.Kp = self.pid_calibrate_Kp_ht
                self.Ki = self.pid_calibrate_Ki_ht
                self.Kd = self.pid_calibrate_Kd_ht
            else:
                self.Kp = self.Kp_ht
                self.Ki = self.Ki_ht
                self.Kd = self.Kd_ht
        else:
            if self.pid_calibrate_Kp:
                self.Kp = self.pid_calibrate_Kp
                self.Ki = self.pid_calibrate_Ki
                self.Kd = self.pid_calibrate_Kd
            else:
                self.Kp = self.config.getfloat('pid_Kp') / PID_PARAM_BASE
                self.Ki = self.config.getfloat('pid_Ki') / PID_PARAM_BASE
                self.Kd = self.config.getfloat('pid_Kd') / PID_PARAM_BASE
        logging.info("dynamically_modify_pid target_temp:%s pid: Kp=%f Ki=%f Kd=%f"%(target_temp, self.Kp, self.Ki, self.Kd))
        if self.Ki:
            self.temp_integ_max = self.heater_max_power / self.Ki
    def temperature_update(self, read_time, temp, target_temp):
        time_diff = read_time - self.prev_temp_time
        # Calculate change of temperature
        temp_diff = temp - self.prev_temp
        if time_diff >= self.min_deriv_time:
            temp_deriv = temp_diff / time_diff
        else:
            temp_deriv = (self.prev_temp_deriv * (self.min_deriv_time-time_diff)
                          + temp_diff) / self.min_deriv_time
        # Calculate accumulated temperature "error"
        temp_err = target_temp - temp
        temp_integ = self.prev_temp_integ + temp_err * time_diff
        temp_integ = max(0., min(self.temp_integ_max, temp_integ))
        # Calculate output
        co = self.Kp*temp_err + self.Ki*temp_integ - self.Kd*temp_deriv
        #logging.debug("pid: %f@%.3f -> diff=%f deriv=%f err=%f integ=%f co=%d",
        #    temp, read_time, temp_diff, temp_deriv, temp_err, temp_integ, co)
        bounded_co = max(0., min(self.heater_max_power, co))
       # self.powerpin = self.printer.lookup_object("power_pin")

        # bounded_co = max(0., min(self.heater_max_power, co))
        # if bounded_co == self.heater_max_power:
        #     if self.oldco == 0:
        #       #  self.powerpin.set_power_pin(0)
        #         self.oldco = self.heater_max_power
        #     else:
        #         if self.oldco == self.heater_max_power:
        #           #  self.powerpin.set_power_pin(1)
        #
        #         self.oldco = 0
        self.heater.set_pwm(read_time, bounded_co)
        # Store state for next measurement
        self.prev_temp = temp
        self.prev_temp_time = read_time
        self.prev_temp_deriv = temp_deriv
        if co == bounded_co:
            self.prev_temp_integ = temp_integ

        if self.heater.name == "heater_bed":
            heater_bed = self.printer.lookup_object('heater_bed')
            if target_temp == 0:
                # 未在加热
                self.heating = False
                self.heater_bed_state = 0
            elif target_temp + PID_SETTLE_DELTA < temp:
                # 已超目标温度
                self.heating = False
                self.heater_bed_state = 2
            elif target_temp and self.check_busy(self.printer.get_reactor().monotonic(), temp, target_temp) == True:
                # 加热中
                self.heating = True
                self.heater_bed_state = 1
            else:
                # 已达到目标温度
                self.heating = False
                self.heater_bed_state = 2
            heater_bed.heater_bed_state = self.heater_bed_state
            heater_bed.last_pwm_value = bounded_co
    def check_busy(self, eventtime, smoothed_temp, target_temp):
        temp_diff = target_temp - smoothed_temp
        return (abs(temp_diff) > PID_SETTLE_DELTA
                or abs(self.prev_temp_deriv) > PID_SETTLE_SLOPE)


######################################################################
# Sensor and heater lookup
######################################################################

class PrinterHeaters:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.sensor_factories = {}
        self.heaters = {}
        self.gcode_id_to_sensor = {}
        self.available_heaters = []
        self.available_sensors = []
        self.has_started = self.have_load_sensors = False
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("gcode:request_restart",
                                            self.turn_off_all_heaters)
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("TURN_OFF_HEATERS", self.cmd_TURN_OFF_HEATERS,
                               desc=self.cmd_TURN_OFF_HEATERS_help)
        gcode.register_command("M105", self.cmd_M105, when_not_ready=True)
        gcode.register_command("TEMPERATURE_WAIT", self.cmd_TEMPERATURE_WAIT,
                               desc=self.cmd_TEMPERATURE_WAIT_help)
        # Register webhooks
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("breakheater", self._handle_breakheater)
        self.can_break=False
        self.can_break_flag = 0
        self.extruder_temperature_wait = False
        self.bed_temperature_wait = False
    def _handle_breakheater(self,web_request):
        reactor = self.printer.get_reactor()
        for heater in self.heaters.values():
            eventtime = reactor.monotonic()
            if heater.check_busy(eventtime):
                self.can_break = True

    def load_config(self, config):
        self.have_load_sensors = True
        # Load default temperature sensors
        pconfig = self.printer.lookup_object('configfile')
        dir_name = os.path.dirname(__file__)
        filename = os.path.join(dir_name, 'temperature_sensors.cfg')
        try:
            dconfig = pconfig.read_config(filename)
        except Exception:
            raise config.config_error("Cannot load config '%s'" % (filename,))
        for c in dconfig.get_prefix_sections(''):
            self.printer.load_object(dconfig, c.get_name())
    def add_sensor_factory(self, sensor_type, sensor_factory):
        self.sensor_factories[sensor_type] = sensor_factory
    def setup_heater(self, config, gcode_id=None):
        heater_name = config.get_name().split()[-1]
        if heater_name in self.heaters:
            raise config.error("Heater %s already registered" % (heater_name,))
        # Setup sensor
        sensor = self.setup_sensor(config)
        # Create heater
        self.heaters[heater_name] = heater = Heater(config, sensor)
        self.register_sensor(config, heater, gcode_id)
        self.available_heaters.append(config.get_name())
        return heater
    def get_all_heaters(self):
        return self.available_heaters
    def lookup_heater(self, heater_name):
        if heater_name not in self.heaters:
            raise self.printer.config_error(
                "Unknown heater '%s'" % (heater_name,))
        return self.heaters[heater_name]
    def setup_sensor(self, config):
        if not self.have_load_sensors:
            self.load_config(config)
        sensor_type = config.get('sensor_type')
        if sensor_type not in self.sensor_factories:
            raise self.printer.config_error(
                "Unknown temperature sensor '%s'" % (sensor_type,))
        if sensor_type == 'NTC 100K beta 3950':
            config.deprecate('sensor_type', 'NTC 100K beta 3950')
        return self.sensor_factories[sensor_type](config)
    def register_sensor(self, config, psensor, gcode_id=None):
        self.available_sensors.append(config.get_name())
        if gcode_id is None:
            gcode_id = config.get('gcode_id', None)
            if gcode_id is None:
                return
        if gcode_id in self.gcode_id_to_sensor:
            raise self.printer.config_error(
                "G-Code sensor id %s already registered" % (gcode_id,))
        self.gcode_id_to_sensor[gcode_id] = psensor
    def get_status(self, eventtime):
        return {'available_heaters': self.available_heaters,
                'available_sensors': self.available_sensors,
                'extruder_temperature_wait': self.extruder_temperature_wait,
                'bed_temperature_wait': self.bed_temperature_wait}
    def turn_off_all_heaters(self, print_time=0.):
        for heater in self.heaters.values():
            heater.set_temp(0.)

    cmd_TURN_OFF_HEATERS_help = "Turn off all heaters"
    def cmd_TURN_OFF_HEATERS(self, gcmd):
        self.turn_off_all_heaters()
    # G-Code M105 temperature reporting
    def _handle_ready(self):
        self.has_started = True
    def _get_temp(self, eventtime):
        # Tn:XXX /YYY B:XXX /YYY
        out = []
        if self.has_started:
            for gcode_id, sensor in sorted(self.gcode_id_to_sensor.items()):
                cur, target = sensor.get_temp(eventtime)
                out.append("%s:%.1f /%.1f" % (gcode_id, cur, target))
        if not out:
            return "T:0"
        return " ".join(out)
    def cmd_M105(self, gcmd):
        # Get Extruder Temperature
        reactor = self.printer.get_reactor()
        msg = self._get_temp(reactor.monotonic())
        did_ack = gcmd.ack(msg)
        if not did_ack:
            gcmd.respond_raw(msg)
    def _wait_for_temperature(self, heater):
        # Helper to wait on heater.check_busy() and report M105 temperatures
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        toolhead = self.printer.lookup_object("toolhead")
        gcode = self.printer.lookup_object("gcode")
        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()
        self.can_break_flag = 1
        self.can_break = False
        if "heater_bed" in heater.name:
            self.bed_temperature_wait = True
        else:
            self.extruder_temperature_wait = True
        while not self.printer.is_shutdown() and heater.check_busy(eventtime) :
            if self.can_break:
                self.can_break_flag = 2
                self.can_break = False
                # toolhead._handle_shutdown()
                #toolhead.move_queue.reset()
                # self.turn_off_all_heaters()
                #gcode.run_script("G28")

                break
            print_time = toolhead.get_last_move_time()
            gcode.respond_raw(self._get_temp(eventtime))
            eventtime = reactor.pause(eventtime + 1.)
        if self.can_break_flag != 2:
            self.can_break_flag = 3
        if "heater_bed" in heater.name:
            self.bed_temperature_wait = False
        else:
            self.extruder_temperature_wait = False
    def set_temperature(self, heater, temp, wait=False):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.register_lookahead_callback((lambda pt: None))
        heater.set_temp(temp)
        if wait and temp:
            self._wait_for_temperature(heater)
    cmd_TEMPERATURE_WAIT_help = "Wait for a temperature on a sensor"
    def cmd_TEMPERATURE_WAIT(self, gcmd):
        sensor_name = gcmd.get('SENSOR')
        if sensor_name not in self.available_sensors:
            raise gcmd.error("Unknown sensor '%s'" % (sensor_name,))
        min_temp = gcmd.get_float('MINIMUM', float('-inf'))
        max_temp = gcmd.get_float('MAXIMUM', float('inf'), above=min_temp)
        if min_temp == float('-inf') and max_temp == float('inf'):
            raise gcmd.error(
                "Error on 'TEMPERATURE_WAIT': missing MINIMUM or MAXIMUM.")
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        if sensor_name in self.heaters:
            sensor = self.heaters[sensor_name]
        else:
            sensor = self.printer.lookup_object(sensor_name)
        toolhead = self.printer.lookup_object("toolhead")
        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()
        while not self.printer.is_shutdown() and not self.can_break:
            temp, target = sensor.get_temp(eventtime)
            if temp >= min_temp and temp <= max_temp:
                return
            print_time = toolhead.get_last_move_time()
            gcmd.respond_raw(self._get_temp(eventtime))
            eventtime = reactor.pause(eventtime + 1.)

def load_config(config):
    return PrinterHeaters(config)
