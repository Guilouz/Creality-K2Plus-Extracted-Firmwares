import mcu, logging

"""
[z_align]
quick_speed: 60 # mm/s  下降速度
slow_speed: 20 # mm/s  探测速度
rising_dist: 20 # mm  首次探测到光电后的上升距离
filter_cnt: 10 # 连续触发限位的次数，用作滤波
timeout: 30 # s 单次探测超时时间
retries: 5 # 重试次数
retry_tolerance: 10  # 两个光电的调整允许的最大偏差 10步 步距是0.0025mm
endstop_pin_z: PA15  # 光电触发
endstop_pin_z1: PA8  # 光电触发
zd_up: 0  # 步进电机远离限位开关的电平
zes_untrig: 1  # 限位开关未触发时的电平
"""

MOTOR_PROTECT_ERROR = -10001

class CommandError(Exception):
    pass

class Zalign:
    error = CommandError
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.full_steps_pre_rev = 200
        self.distance_ratio = self.config.getsection('z_align').getfloat('distance_ratio')
        self.quickSpeed = self.config.getsection('z_align').getint('quick_speed')
        self.slowSpeed = self.config.getsection('z_align').getint('slow_speed')
        self.risingDist = self.config.getsection('z_align').getint('rising_dist')
        self.safeDist = self.config.getsection('z_align').getint('safe_dist')
        self.filterCnt = self.config.getsection('z_align').getint('filter_cnt')
        self.timeout = self.config.getsection('z_align').getint('timeout')
        self.retries = self.config.getsection('z_align').getint('retries')
        self.retry_tolerance = self.config.getsection('z_align').getint('retry_tolerance')
        self.endstop_pin_z = self.config.getsection('z_align').get('endstop_pin_z')                                       
        self.endstop_pin_z1 = self.config.getsection('z_align').get('endstop_pin_z1') 
        self.zd_up = self.config.getsection('z_align').getint('zd_up')
        self.zes_untrig = self.config.getsection('z_align').getint('zes_untrig')
        self.zmax_safe_pox_diff = self.config.getsection('z_align').getint('zmax_safe_pox_diff')
        self.mcu = mcu.get_printer_mcu(self.printer, "mcu")
        self.oidz = self.mcu.create_oid()
        self.mcu.register_config_callback(self._build_config)
        self.cur_retries = 0
        self.gcode = config.get_printer().lookup_object('gcode')
        self.gcode.register_command("GET_MAX_Z", self.cmd_GET_MAX_Z)
        self.gcode.register_command("ZDOWN", self.cmd_ZDOWN)
        self.gcode.register_command("ZDOWN_SWITCH", self.cmd_ZDOWN_SWITCH)
        self.gcode.register_command("ZDOWN_FORCE_STOP", self.cmd_ZDOWN_FORCE_STOP)
        self.zdown_switch_enable = 0
        self.z_align_force_stop = None
        self.force_stop_flag = False
        self.is_already_zodwn = False
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("zdown_force_stop", self.zdown_force_stop)
    def zdown_force_stop(self, web_request):
        self.force_stop_flag = True
        self.gcode.respond_info("zdown_force_stop start")
        self.z_align_force_stop.send([self.oidz])
        self.gcode.respond_info("zdown_force_stop end")
        web_request.send({"result": "success"})
    def _build_config(self):  
        stepper_indx_z = 0
        stepper_indx_z1 = 1   
        step_pin_z = self.config.getsection('stepper_z').get('step_pin') 
        step_pin_z1 = self.config.getsection('stepper_z1').get('step_pin')    
        dir_pin_z = self.config.getsection('stepper_z').get('dir_pin') 
        dir_pin_z1 = self.config.getsection('stepper_z1').get('dir_pin')                                                                                                            

        config_z_align = "config_z_align oid=%d"%self.oidz
        config_z_align_add_z = "config_z_align_add oid=%d z_indx=%d zs_pin=%s" \
                                " zd_pin=%s zd_up=%d zes_pin=%s zes_untrig=%d" % (
                                    self.oidz, stepper_indx_z, step_pin_z, dir_pin_z, self.zd_up, self.endstop_pin_z, self.zes_untrig)
        config_z_align_add_z1 = "config_z_align_add oid=%d z_indx=%d zs_pin=%s" \
                                " zd_pin=%s zd_up=%d zes_pin=%s zes_untrig=%d" % (
                                    self.oidz, stepper_indx_z1, step_pin_z1, dir_pin_z1, self.zd_up, self.endstop_pin_z1, self.zes_untrig)
        logging.info(config_z_align)
        self.mcu.add_config_cmd(config_z_align)
        logging.info(config_z_align)
        self.mcu.add_config_cmd(config_z_align_add_z)
        logging.info(config_z_align)
        self.mcu.add_config_cmd(config_z_align_add_z1)
        self.z_align_force_stop = self.mcu.lookup_command("z_align_force_stop oid=%c", cq=None)
    def cmd_ZDOWN_FORCE_STOP(self, gcmd):
        self.force_stop_flag = True
        self.gcode.respond_info("zdown_force_stop start")
        self.z_align_force_stop.send([self.oidz])
        self.gcode.respond_info("zdown_force_stop end")
    def cmd_ZDOWN_SWITCH(self, gcmd):
        self.zdown_switch_enable = gcmd.get_int('ENABLE', default=1)
    def cmd_GET_MAX_Z(self, gcmd):
        self.gcode.run_script_from_command("BED_MESH_CLEAR")
        query_finetuning = self.mcu.lookup_query_command("query_finetuning oid=%c enable=%c speed=%u maxDist=%u filterCnt=%c",
                                                      "finetuning_status oid=%c flag=%i steps=%u", oid=self.oidz)
        rotation_distance = self.config.getsection('stepper_z').getfloat('rotation_distance')  # 8
        microsteps = self.config.getsection('stepper_z').getfloat('microsteps')  # 16

        mcu_freq = self.mcu._serial.msgparser.get_constant_float('CLOCK_FREQ')
        subdivision = self.full_steps_pre_rev*microsteps # 200*16 = 3200
        step_distance = rotation_distance/subdivision # 8/3200 = 0.0025mm
        quickSpeedTicks = int(1/(self.quickSpeed/step_distance)*mcu_freq/2) # 除以2才和klipper的速度一致
        enable = 1
        maxDist = int(360/8 * 200 * 16 * 2) # 乘以2才和klipper的步数一致 steps = position_max/rotation_distance  * 200 * microsteps
        self.filterCnt
        self.gcode.run_script_from_command("M84")
        self.gcode.run_script_from_command("G28")
        self.gcode.run_script_from_command("G4 P3000")
        query_finetuning.send([self.oidz, enable, quickSpeedTicks, maxDist, self.filterCnt])
        gcode_move = self.printer.lookup_object('gcode_move')
        cur_z_pos = gcode_move.last_position[2]
        reactor = self.printer.get_reactor()
        self.mcu._serial.finetuning_status = {}
        curtime = reactor.monotonic()
        steps = 0
        while True:
            self.gcode.respond_info(str(self.mcu._serial.finetuning_status))
            nowtime = reactor.monotonic()
            usetime = nowtime-curtime
            flag = self.mcu._serial.finetuning_status.get("flag", 0)
            if flag == 1:
                steps = int(self.mcu._serial.finetuning_status.get("steps", 0))
                break
            elif flag == 2:
                self.gcode.respond_info("finetuning_status mcu timeout")
                break
            elif usetime > self.timeout:
                self.gcode.respond_info("finetuning_status 30s timeout")
                break
            reactor.pause(reactor.monotonic() + 1.0)
        self.gcode.respond_info("finetuning_status result: %s+%s=%s" % (cur_z_pos, steps*0.0025/2, steps*0.0025/2+5))
        toolhead = self.printer.lookup_object('toolhead')
        now_pos = toolhead.get_position()
        now_pos[2] = self.config.getsection('stepper_z').getfloat('position_max')
        toolhead.set_position(now_pos, homing_axes=(2,))
        now_pos = toolhead.get_position()
        toolhead.set_position(now_pos, homing_axes=(2,))
        now_pos[2] = now_pos[2] - 306
        gcmd = 'G1 F%d X%.3f Y%.3f Z%.3f' % (30 * 60, now_pos[0], now_pos[1], now_pos[2])
        self.gcode.run_script_from_command(gcmd)
        self.gcode.run_script_from_command("G28 Z")

    def cmd_ZDOWN(self, gcmd):
        self.gcode.run_script_from_command("RESTORE_Z_LIMIT")
        self.gcode.run_script_from_command("BED_MESH_CLEAR")
        reactor = self.printer.get_reactor()
        query_z_align = self.mcu.lookup_query_command("query_z_align oid=%c enable=%c quickSpeed=%u slowSpeed=%u risingDist=%u filterCnt=%c safeDist=%u",
                                                      "z_align_status oid=%c flag=%i deltaError1=%i", oid=self.oidz)

        rotation_distance = self.config.getsection('stepper_z').getfloat('rotation_distance')  # 8
        microsteps = self.config.getsection('stepper_z').getfloat('microsteps')  # 16

        mcu_freq = self.mcu._serial.msgparser.get_constant_float('CLOCK_FREQ')
        subdivision = self.full_steps_pre_rev*microsteps # 200*16 = 3200
        step_distance = rotation_distance/subdivision # 8/3200 = 0.0025mm
        quickSpeedTicks = int(1/(self.quickSpeed/step_distance)*mcu_freq/2) # 除以2才和klipper的速度一致
        slowSpeedTicks = int(1/(self.slowSpeed/step_distance)*mcu_freq/2) # 除以2才和klipper的速度一致
        risingDistStep = int(self.risingDist/step_distance)*2 # 乘2才和klipper的步数一致
        safeDistStep = int(self.safeDist/step_distance)*2
        enable = 1
        def run_cmd(cur_retries):
            deltaError = 0
            msg = "send query_z_align cur_retries:%s oid=%d enable=%d quickSpeed=%s slowSpeed=%s risingDist=%s filterCnt:%s safeDist:%s"%(cur_retries, self.oidz, enable, quickSpeedTicks, slowSpeedTicks, risingDistStep, self.filterCnt, safeDistStep)
            params = query_z_align.send([self.oidz, enable, quickSpeedTicks, slowSpeedTicks, risingDistStep, self.filterCnt, safeDistStep])
            # {'oid': 1, 'flag': 0, 'deltaError1': 5, '#name': 'z_align_status', '#sent_time': 49.895344040666664, '#receive_time': 49.995911207}
            self.gcode.respond_info(msg)
            curtime = reactor.monotonic()
            reactor.pause(reactor.monotonic() + 1.0)
            while True:
                nowtime = reactor.monotonic()
                usetime = nowtime-curtime
                if self.force_stop_flag:
                    self.force_stop_flag = False
                    return MOTOR_PROTECT_ERROR
                if usetime > self.timeout:
                    self.gcode._respond_error("""{"code":"key351", "msg":"z_align ZDOWN timeout:%ss result: %s", "values":[]}"""%(self.timeout, str(self.mcu._serial.z_align_status)))
                    return -10000
                if self.mcu._serial.z_align_status.get("flag", 0) == 1:
                    self.gcode.respond_info("usetime:%s z_align_status :%s"%(usetime, str(self.mcu._serial.z_align_status)))
                    deltaError = int(self.mcu._serial.z_align_status.get("deltaError1", 0))
                    break
                elif self.mcu._serial.z_align_status.get("flag", 0) == 2:
                    self.gcode._respond_error("""{"code":"key357", "msg":"光电开关状态异常或者是热床过于倾斜", "values":[]}""")
                    reactor.pause(reactor.monotonic() + 5.0)
                    return MOTOR_PROTECT_ERROR
                reactor.pause(reactor.monotonic() + 0.1)
            return deltaError
        toolhead = self.printer.lookup_object('toolhead')
        now_pos = toolhead.get_position()
        toolhead.set_position(now_pos, homing_axes=(2,))
        self.gcode.run_script_from_command("SET_STEPPER_ENABLE STEPPER=stepper_z ENABLE=1")
        self.gcode.run_script_from_command("SET_STEPPER_ENABLE STEPPER=stepper_z1 ENABLE=1")
        self.cur_retries = 0
        while True:
            if self.cur_retries < self.retries:
                deltaError = run_cmd(self.cur_retries)
            else:
                self.gcode._respond_error("""{"code":"key352", "msg":"z_align ZDOWN too many retries: %s, deltaError:%s retry_tolerance:%s", "values":[]}"""%(deltaError, self.retry_tolerance, str(self.retries)))
                break
            if deltaError == -10000:
                # timeout 
                toolhead = self.printer.lookup_object('toolhead')
                now_pos = toolhead.get_position()
                toolhead.set_position(now_pos, homing_axes=(0,1,2))
                gcmd = 'G1 F%d X%.3f Y%.3f Z%.3f' % (1000, now_pos[0]+0.001, now_pos[1], now_pos[2])
                self.gcode.run_script_from_command(gcmd)
                self.gcode.run_script_from_command("M84")
                break
            elif deltaError == MOTOR_PROTECT_ERROR:
                self.gcode.run_script_from_command("M84")
                self.gcode.respond_info("zdown_force_stop success")
                return MOTOR_PROTECT_ERROR
            if abs(deltaError) < self.retry_tolerance:
                self.gcode.respond_info("ZDOWN end")
                break
            self.cur_retries += 1
        if toolhead.G29_flag == False:
            offset_value = 0
            now_pos = toolhead.get_position()
            real_zmax = self.read_real_zmax()
            za = real_zmax + offset_value
            # 在恢复双Z校准值前,先恢复设置Z轴最大高度值
            toolhead.set_position([now_pos[0], now_pos[1], za, now_pos[3]], homing_axes=(2,))
            logging.info("ZDOWN G29_flag is Fasle, real_zmax:%s offset_value:%s za:%s now_pos:%s" % (real_zmax, offset_value, za,str(now_pos)))
            self.gcode.run_script_from_command("G91\nG1 Z-10 F600\nG90")
            self.gcode.run_script_from_command("M400")
            self.gcode.run_script_from_command("ADJUST_STEPPERS")
            self.gcode.run_script_from_command("M400")
            now_pos = toolhead.get_position()
            logging.info("ZDOWN G29_flag is Fasle, after ADJUST_STEPPERS now_pos:%s"%str(now_pos))
        else:
            self.gcode.run_script_from_command("M400")
            toolhead = self.printer.lookup_object('toolhead')
            now_pos = toolhead.get_position()
            now_pos[2] = self.config.getsection('stepper_z').getfloat('position_max')
            toolhead.set_position(now_pos, homing_axes=(2,))
            logging.info("ZDOWN G29_flag is True, set zmax:%s" % str(now_pos))
        return 0

    def read_real_zmax(self):
        import os,json
        max_z = self.config.getsection('stepper_z').getfloat('position_max', default=360)
        logging.info("stepper_z position_max:%s" % max_z)
        data = max_z - 10
        if self.config.has_section("z_tilt"):
            z_tilt = self.printer.lookup_object('z_tilt')
            if os.path.exists(z_tilt.real_zmax_path):
                try:
                    with open(z_tilt.real_zmax_path, "r") as f:
                        data = json.loads(f.read()).get("zmax", 0)
                        if data > max_z:
                            data = max_z - 10
                except Exception as err:
                    logging.error(err)
        self.gcode.respond_info("real_zmax:%s"%data)
        return data  

def load_config(config):
    return Zalign(config)
