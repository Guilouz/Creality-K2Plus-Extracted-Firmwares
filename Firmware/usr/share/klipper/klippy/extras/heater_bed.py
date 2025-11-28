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
        gcode.register_command("BED_HEADER_SET_HEARER_POWER_MAX", self.cmd_BED_HEADER_SET_HEARER_POWER_MAX)
        gcode.register_command("BED_HEADER_POWER_CALIBRATION", self.cmd_BED_HEADER_POWER_CALIBRATION)
        self.max_temp = config.getfloat('max_temp', above=0.0)
        self.heater_bed_state = 0
        self.last_pwm_value = 0
        #power_calibration
        self.power_calibration_enable = config.getboolean('power_calibration_enable', 0)
        self.power_calibration_wait_even_heating_time = config.getfloat('power_calibration_wait_even_heating_time', 0)
        # self.power_calibration_even_heating_temp = config.getfloat('power_calibration_even_heating_temp', 80)
        self.power_calibration_start_temp = config.getfloat('power_calibration_start_temp', 70)
        self.power_calibration_end_temp = config.getfloat('power_calibration_end_temp', 100)
        self.power_calibration_sample_interval = config.getfloat('power_calibration_sample_interval', 0.1)
        self.power_calibration_sample_end_temp_offset = config.getfloat('power_calibration_sample_end_temp_offset', 10)
        self.power_calibration_sample_start_temp_offset = config.getfloat('power_calibration_sample_start_temp_offset', 10)
        self.power_calibration_full_power_110_220_slop_divide = config.getfloat('power_calibration_full_power_110_220_slop_divide', 0.2)
        self.power_calibration_power_110 = config.getfloat('power_calibration_power_110', 1)
        self.power_calibration_power_220 = config.getfloat('power_calibration_power_220', 0.75)
        self.power_calibration_max_calibrate_times = config.getint('power_calibration_max_calibrate_times', 10)
        self.power_calibration_retry_times = config.getint('power_calibration_retry_times', 3)
        self.power_calibration_heating_slope_low = config.getfloat('power_calibration_heating_slope_low', 0.45)
        self.power_calibration_heating_slope_high = config.getfloat('power_calibration_heating_slope_high', 0.67)
        # power_pid_calibration
        self.pid = {"Kp": [config.getfloat('pid_220_kp', 18.578), config.getfloat('pid_110_kp', 52.98)],
                    "Ki": [config.getfloat('pid_220_ki', 0.314), config.getfloat('pid_110_ki', 0.849)],
                    "Kd": [config.getfloat('pid_220_kd', 274.489), config.getfloat('pid_110_kd', 826.491)]}

    def cmd_M140(self, gcmd, wait=False):
        # Set Bed Temperature
        temp = gcmd.get_float('S', 0.)
        if temp > self.max_temp - 15.0:
            temp = self.max_temp - 15.0
        pheaters = self.printer.lookup_object('heaters')
        pheaters.set_temperature(self.heater, temp, wait)
    def cmd_M190(self, gcmd):
        # Set Bed Temperature and Wait
        self.cmd_M140(gcmd, wait=True)
    def cmd_BED_HEADER_SET_HEARER_POWER_MAX(self, gcmd):
        max_power = gcmd.get_float('S', 1)
        self.heater.max_power = max_power
        self.heater.control.heater_max_power = max_power
    def cmd_BED_HEADER_POWER_CALIBRATION(self, gcmd):
        """
        bed_header_power_calibration
        1.preheat to 80 degree
        2.heating 70-100 degree,sample 0.1s,full power
        3.if slope < 0.4, may be 110v heater return
        4.find max power, slope 0.45-0.67
        :param gcmd:
        :return:
        """
        def set_temperature(temperature, power=1, wait=True):
            pheaters = self.printer.lookup_object('heaters')
            self.heater.max_power  = power
            self.heater.control.heater_max_power = power
            pheaters.set_temperature(self.heater, temperature,wait)
        def get_heater_slope(b_temp,e_temp, max_power_=1, wait_even_heating=False):
            gcode_obj.respond_info(f"get_heater_slope b_temp:{b_temp},e_temp:{e_temp},max_power_:{max_power_}")
            set_temperature(b_temp,1,True)
            if wait_even_heating:
                reactor_obj.pause(reactor_obj.monotonic() + self.power_calibration_wait_even_heating_time)
            set_temperature(e_temp,max_power_,wait=False)
            sample_times = []
            sample_temps = []
            current_time = reactor_obj.monotonic()
            while (n_temp:=self.heater.last_temp) < e_temp - self.power_calibration_sample_end_temp_offset:
                if n_temp > b_temp + self.power_calibration_sample_start_temp_offset:
                    sample_temps.append(n_temp)
                    sample_times.append(reactor_obj.monotonic())
                if reactor_obj.pause(reactor_obj.monotonic() + .1) - current_time > 180: break
            set_temperature(0,1,False)
            sample_temps = sample_temps[1:]
            sample_times = sample_times[1:]
            import numpy as np
            x = np.array(sample_times)
            y = np.array(sample_temps)
            slope_, intercept = np.polyfit(x, y, 1)
            gcode_obj.respond_info(f"heating_slope:{slope_}")
            return slope_
        def if_power_is_220v():
            is_220v = True
            current_temp = self.heater.last_temp
            need_preheat = False
            if current_temp <=self.power_calibration_end_temp-30:
                start_temp = current_temp
                target_temp = current_temp + 30
            else:
                need_preheat = True
                start_temp = self.power_calibration_end_temp - 30
                target_temp = self.power_calibration_end_temp
            gcode_obj.respond_info(f"start_temp:{start_temp},target_temp:{target_temp}")
            if need_preheat:
                #① preheat to start_temp
                set_temperature(start_temp,1,True)
            reactor_obj.pause(reactor_obj.monotonic() + self.power_calibration_wait_even_heating_time)
            #② if slope < power_calibration_full_power_110_220_slop_divide, may be 110v heater return
            slope = get_heater_slope(start_temp,target_temp,0.5,wait_even_heating=False)
            if slope <= self.power_calibration_full_power_110_220_slop_divide:
                is_220v = False
                gcode_obj.respond_info(f"max_power:1, slope:{slope} <= full_power_110_220_slop_divide:{self.power_calibration_full_power_110_220_slop_divide}")
                gcode_obj.respond_info(f'May be 110v heater,set max_power:{self.power_calibration_power_110}')
                self.heater.max_power = self.power_calibration_power_110
                self.heater.control.heater_max_power = self.power_calibration_power_110
            elif not self.power_calibration_enable:
                gcode_obj.respond_info(f"max_power:1, slope:{slope} > full_power_110_220_slop_divide:{self.power_calibration_full_power_110_220_slop_divide}")
                gcode_obj.respond_info(f'May be 220v heater,set max_power:{self.power_calibration_power_220}')
                self.heater.max_power = self.power_calibration_power_220
                self.heater.control.heater_max_power = self.power_calibration_power_220
            return is_220v
        def power_220v_calibration():
            low_power = 0
            high_power = 1
            stop_calibrate = False
            for retry_count in range(self.power_calibration_retry_times):
                if stop_calibrate:
                    break
                gcode_obj.respond_info(f'Start power calibration')
                calibrate_times = self.power_calibration_max_calibrate_times+1
                while calibrate_times:=calibrate_times-1:
                    gcode_obj.respond_info(f"calibrate_times:{calibrate_times}")
                    max_power = (low_power + high_power) / 2
                    slope = get_heater_slope(self.power_calibration_start_temp,self.power_calibration_end_temp,max_power)
                    if slope < self.power_calibration_heating_slope_low:
                        gcode_obj.respond_info(f"max_power:{max_power},slope:{slope} < heating_slope_low:{self.power_calibration_heating_slope_low}")
                        low_power = max_power
                    elif slope > self.power_calibration_heating_slope_high:
                        gcode_obj.respond_info(f"max_power:{max_power},slope:{slope} > heating_slope_high:{self.power_calibration_heating_slope_high}")
                        high_power = max_power
                    else:
                        gcode_obj.respond_info(f"max_power:{max_power}, slope:{slope} 0.45-0.67")
                        gcode_obj.respond_info(f"power calibration success")
                        configfile.set('heater_bed', 'max_power', f'{max_power:.6f}')
                        gcode_obj.run_script_from_command('CXSAVE_CONFIG')
                        stop_calibrate = True
                        break
                    if calibrate_times == 1:
                        gcode_obj.respond_info(f"power calibration fail")
                        gcode_obj.respond_info(f"max_power:{max_power}, slope:{slope}")
                        gcode_obj.respond_info(f"low_power:{low_power}, high_power:{high_power}")
        gcode_obj = self.printer.lookup_object('gcode')
        reactor_obj = self.printer.get_reactor()
        configfile = self.printer.lookup_object('configfile')
        finish_temp = gcmd.get_float('S', 0)
        if not if_power_is_220v() or not self.power_calibration_enable :
            configfile.set('heater_bed', 'max_power', f'{self.heater.max_power:.6f}')
            set_temperature(finish_temp,self.heater.max_power,True)
            if self.heater.control.__class__.__name__ == "ControlPID":
                raw_pid = {k: v[int(self.heater.max_power)] if self.heater.max_power.is_integer() else v[0] for k, v in self.pid.items()}
                (lambda Kp, Ki, Kd: (setattr(self.heater.control, 'Kp', Kp), setattr(self.heater.control, 'Ki', Ki), setattr(self.heater.control, 'Kd', Kd)))\
                    (**{k: v / 255. for k, v in raw_pid.items()})
                configfile.set('heater_bed', 'control', 'pid')
                configfile.set('heater_bed', 'pid_kp', f'{raw_pid["Kp"]:.6f}')
                configfile.set('heater_bed', 'pid_ki', f'{raw_pid["Ki"]:.6f}')
                configfile.set('heater_bed', 'pid_kd', f'{raw_pid["Kd"]:.6f}')
            gcode_obj.run_script_from_command('CXSAVE_CONFIG')
            return
        #③ Find the maximum power that satisfies the slope range
        power_220v_calibration()
        set_temperature(finish_temp,self.heater.max_power,True)
def load_config(config):
    return PrinterHeaterBed(config)
