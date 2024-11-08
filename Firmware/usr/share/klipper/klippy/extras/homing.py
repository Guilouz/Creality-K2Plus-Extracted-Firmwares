# Helper code for implementing homing operations
#
# Copyright (C) 2016-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math, json, os
from extras.z_align import MOTOR_PROTECT_ERROR

HOMING_START_DELAY = 0.001
ENDSTOP_SAMPLE_TIME = .000015
ENDSTOP_SAMPLE_COUNT = 4

# Return a completion that completes when all completions in a list complete
def multi_complete(printer, completions):
    if len(completions) == 1:
        return completions[0]
    # Build completion that waits for all completions
    reactor = printer.get_reactor()
    cp = reactor.register_callback(lambda e: [c.wait() for c in completions])
    # If any completion indicates an error, then exit main completion early
    for c in completions:
        reactor.register_callback(
            lambda e, c=c: cp.complete(1) if c.wait() else 0)
    return cp

# Tracking of stepper positions during a homing/probing move
class StepperPosition:
    def __init__(self, stepper, endstop_name):
        self.stepper = stepper
        self.endstop_name = endstop_name
        self.stepper_name = stepper.get_name()
        self.start_pos = stepper.get_mcu_position()
        self.halt_pos = self.trig_pos = None
    def note_home_end(self, trigger_time):
        self.halt_pos = self.stepper.get_mcu_position()
        self.trig_pos = self.stepper.get_past_mcu_position(trigger_time)

# Implementation of homing/probing moves
class HomingMove:
    def __init__(self, printer, endstops, toolhead=None):
        self.printer = printer
        self.endstops = endstops
        if toolhead is None:
            toolhead = printer.lookup_object('toolhead')

        self.prtouch_v3 = printer.lookup_object('prtouch_v3')
        self.prtouch_v3.z_full_movement_flag = False
        self.toolhead = toolhead
        self.stepper_positions = []
    def get_mcu_endstops(self):
        return [es for es, name in self.endstops]
    def _calc_endstop_rate(self, mcu_endstop, movepos, speed):
        startpos = self.toolhead.get_position()
        axes_d = [mp - sp for mp, sp in zip(movepos, startpos)]
        move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
        move_t = move_d / speed
        max_steps = max([(abs(s.calc_position_from_coord(startpos)
                              - s.calc_position_from_coord(movepos))
                          / s.get_step_dist())
                         for s in mcu_endstop.get_steppers()])
        if max_steps <= 0.:
            return .001
        return move_t / max_steps
    def calc_toolhead_pos(self, kin_spos, offsets):
        kin_spos = dict(kin_spos)
        kin = self.toolhead.get_kinematics()
        for stepper in kin.get_steppers():
            sname = stepper.get_name()
            kin_spos[sname] += offsets.get(sname, 0) * stepper.get_step_dist()
        thpos = self.toolhead.get_position()
        return list(kin.calc_position(kin_spos))[:3] + thpos[3:]
    def handle_force_stop(self):
        # 拉高pa9 en引脚, 清除运动队列, 查询保护码, 设置xy电机到错误码输出模式
        # self.printer.lookup_object('motor_control').force_stop()
        toolhead = self.printer.lookup_object('toolhead')
        toolhead._handle_shutdown()
        toolhead.reactor.pause(toolhead.reactor.monotonic() + 1.0)
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script_from_command("MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11")
        gcode.run_script_from_command("MOTOR_STALL_MODE DATA=2")
        toolhead.can_pause = True
    def homing_move(self, movepos, speed, probe_pos=False,
                    triggered=True, check_triggered=True):
        # Notify start of homing/probing move
        self.printer.send_event("homing:homing_move_begin", self)
        # Note start location
        self.toolhead.flush_step_generation()
        kin = self.toolhead.get_kinematics()
        kin_spos = {s.get_name(): s.get_commanded_position()
                    for s in kin.get_steppers()}
        
        self.stepper_positions = [ StepperPosition(s, name)
                                   for es, name in self.endstops
                                   for s in es.get_steppers() ]

        # Start endstop checking
        print_time = self.toolhead.get_last_move_time()
        endstop_triggers = []
        for mcu_endstop, name in self.endstops:
            rest_time = self._calc_endstop_rate(mcu_endstop, movepos, speed)
            wait = mcu_endstop.home_start(print_time, ENDSTOP_SAMPLE_TIME,
                                          ENDSTOP_SAMPLE_COUNT, rest_time,
                                          triggered=triggered)
            endstop_triggers.append(wait)
        all_endstop_trigger = multi_complete(self.printer, endstop_triggers)
        self.toolhead.dwell(HOMING_START_DELAY)
        # Issue move
        error = None
        try:
            self.toolhead.drip_move(movepos, speed, all_endstop_trigger)
        except self.printer.command_error as e:
            error = """{"code":"key20", "msg":"Error during homing move: %s", "values": [%s]}""" % (str(e),str(e))
            logging.info("No trigger on %s after full movement, set MOTOR_STALL_MODE DATA=2"%name)
            self.handle_force_stop()
        # Wait for endstops to trigger
        trigger_times = {}
        move_end_print_time = self.toolhead.get_last_move_time()
        for mcu_endstop, name in self.endstops:
            trigger_time = mcu_endstop.home_wait(move_end_print_time)
            if trigger_time > 0.:
                trigger_times[name] = trigger_time
            elif trigger_time < 0. and error is None:
                error = """{"code":"key21", "msg":"Communication timeout during homing %s", "values": ["%s"]}""" % (name, name)
                logging.info("Communication timeout during homing %s, set MOTOR_STALL_MODE DATA=2"%name)
                self.handle_force_stop()
            elif check_triggered and error is None:
                error = """{"code":"key22", "msg":"No trigger on %s after full movement", "values": ["%s"]}""" % (name, name)
                # z轴误触发后,对x、y电机进行切换为错误码输出模式
                if name == "z":
                    error = None
                    self.prtouch_v3.z_full_movement_flag = True
                    logging.info("No trigger on z after full movement, set MOTOR_STALL_MODE DATA=2")
                    gcode = self.printer.lookup_object('gcode')
                    gcode.run_script_from_command("MOTOR_STALL_MODE DATA=2")
                logging.info("No trigger on %s after full movement, set MOTOR_STALL_MODE DATA=2"%name)
                self.handle_force_stop()
        # Determine stepper halt positions
        self.toolhead.flush_step_generation()
        for sp in self.stepper_positions:
            tt = trigger_times.get(sp.endstop_name, move_end_print_time)
            sp.note_home_end(tt)
        if probe_pos:
            halt_steps = {sp.stepper_name: sp.halt_pos - sp.start_pos
                          for sp in self.stepper_positions}
            trig_steps = {sp.stepper_name: sp.trig_pos - sp.start_pos
                          for sp in self.stepper_positions}
            haltpos = trigpos = self.calc_toolhead_pos(kin_spos, trig_steps)
            if trig_steps != halt_steps:
                haltpos = self.calc_toolhead_pos(kin_spos, halt_steps)
        else:
            haltpos = trigpos = movepos
            over_steps = {sp.stepper_name: sp.halt_pos - sp.trig_pos
                          for sp in self.stepper_positions}
            if any(over_steps.values()):
                self.toolhead.set_position(movepos)
                halt_kin_spos = {s.get_name(): s.get_commanded_position()
                                 for s in kin.get_steppers()}
                haltpos = self.calc_toolhead_pos(halt_kin_spos, over_steps)
        self.toolhead.set_position(haltpos)
        # Signal homing/probing move complete
        try:
            self.printer.send_event("homing:homing_move_end", self)
        except self.printer.command_error as e:
            if error is None:
                error = str(e)
        if error is not None:
            raise self.printer.command_error(error)
        return trigpos
    def check_no_movement(self):
        if self.printer.get_start_args().get('debuginput') is not None:
            return None
        for sp in self.stepper_positions:
            if sp.start_pos == sp.trig_pos:
                return sp.endstop_name
        return None
        
# State tracking of homing requests
class Homing:
    def __init__(self, printer):
        self.printer = printer
        self.toolhead = printer.lookup_object('toolhead')
        self.changed_axes = []
        self.trigger_mcu_pos = {}
        self.adjust_pos = {}
        self.stepper_z_sensorless_flag = False
        self.out_z_all = 0
        self.homez_info = None

    def set_axes(self, axes):
        self.changed_axes = axes
    def get_axes(self):
        return self.changed_axes
    def get_trigger_position(self, stepper_name):
        return self.trigger_mcu_pos[stepper_name]
    def set_stepper_adjustment(self, stepper_name, adjustment):
        self.adjust_pos[stepper_name] = adjustment
    def _fill_coord(self, coord):
        # Fill in any None entries in 'coord' with current toolhead position
        thcoord = list(self.toolhead.get_position())
        for i in range(len(coord)):
            if coord[i] is not None:
                thcoord[i] = coord[i]
        return thcoord
    def set_homed_position(self, pos):
        self.toolhead.set_position(self._fill_coord(pos))

    def get_step(self,endstops):
        self.stepper_positions = [ StepperPosition(s, name)
                                   for es, name in endstops
                                   for s in es.get_steppers() ]
        for sp in self.stepper_positions:
            if sp.stepper_name == "stepper_z":
                kin = self.toolhead.get_kinematics()
                halt_step_dist = {s.get_name(): s.get_step_dist()
                                 for s in kin.get_steppers()}
                logging.info("start_pos:%s trig_pos:%s halt_step_dist:%s" % (sp.start_pos,sp.trig_pos,halt_step_dist[sp.stepper_name]))
                return [sp.start_pos, halt_step_dist[sp.stepper_name]]
        return None
    def home_rails(self, rails, forcepos, movepos):
        # Notify of upcoming homing operation
        self.printer.send_event("homing:home_rails_begin", self, rails)
        # Alter kinematics class to think printer is at forcepos
        homing_axes = [axis for axis in range(3) if forcepos[axis] is not None]
        startpos = self._fill_coord(forcepos)
        homepos = self._fill_coord(movepos)
        self.toolhead.set_position(startpos, homing_axes=homing_axes)
        # Perform first home
        endstops = [es for rail in rails for es in rail.get_endstops()]
        hi = rails[0].get_homing_info()
        # 获取归零开始时的z电机step
        _homez_info = self.get_step(endstops)
        if _homez_info is not None:
            # 记录start_pos halt_pos step_dist数据
            self.homez_info = _homez_info
        hmove = HomingMove(self.printer, endstops)
        if self.stepper_z_sensorless_flag:
            hmove.homing_move(homepos, hi.speed*4, False, True, False)
        else:
            hmove.homing_move(homepos, hi.speed)      

        # Perform second home
        if hi.retract_dist:
            # Retract
            startpos = self._fill_coord(forcepos)
            homepos = self._fill_coord(movepos)
            axes_d = [hp - sp for hp, sp in zip(homepos, startpos)]
            move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
            retract_r = min(1., hi.retract_dist / move_d)
            retractpos = [hp - ad * retract_r
                          for hp, ad in zip(homepos, axes_d)]
            self.toolhead.move(retractpos, hi.retract_speed)
            # Home again
            startpos = [rp - ad * retract_r
                        for rp, ad in zip(retractpos, axes_d)]
            self.toolhead.set_position(startpos)
            hmove = HomingMove(self.printer, endstops)
            hmove.homing_move(homepos, hi.second_homing_speed)

            if hmove.check_no_movement() is not None and rails[0].get_name() == "stepper_z":
                hmove.prtouch_v3.z_full_movement_flag = True
                self.printer.send_event("homing:homing_move_end", hmove)


            # 获取停止时后的z电机step
            _homez_info = self.get_step(endstops)
            if _homez_info is not None:
                # 只更新halt_pos数据
                self.out_z_all = abs(self.homez_info[0] - _homez_info[0]) * self.homez_info[1]
            if hmove.check_no_movement() is not None and rails[0].get_name() != "stepper_z":
                logging.info("hmove.check_no_movement %s, set MOTOR_STALL_MODE DATA=2" % hmove.check_no_movement())
                hmove.handle_force_stop()
                raise self.printer.command_error(
                    """{"code":"key23", "msg":"Endstop %s still triggered after retract", "values": ["%s"]}"""
                    % (hmove.check_no_movement(), hmove.check_no_movement()))
        # Signal home operation complete
        self.toolhead.flush_step_generation()
        self.trigger_mcu_pos = {sp.stepper_name: sp.trig_pos
                                for sp in hmove.stepper_positions}
        self.adjust_pos = {}
        self.printer.send_event("homing:home_rails_end", self, rails)
        if any(self.adjust_pos.values()):
            # Apply any homing offsets
            kin = self.toolhead.get_kinematics()
            homepos = self.toolhead.get_position()
            kin_spos = {s.get_name(): (s.get_commanded_position()
                                       + self.adjust_pos.get(s.get_name(), 0.))
                        for s in kin.get_steppers()}
            newpos = kin.calc_position(kin_spos)
            for axis in homing_axes:
                homepos[axis] = newpos[axis]
            self.toolhead.set_position(homepos)

class PrinterHoming:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        # Register g-code commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('RECOVERY_Z_ADJUSTMENT', self.cmd_RECOVERY_Z_ADJUSTMENT)
        gcode.register_command('G28', self.cmd_G28)
        gcode.register_command('STEPPER_Z_SENEORLESS', self.cmd_STEPPER_Z_SENEORLESS)
        self.probe_type = ""
        if config.has_section('prtouch_v2'):
            self.probe_type = "prtouch_v2"
        elif config.has_section('bltouch'):
            self.probe_type = "bltouch"
        self.z_move = 0
    def manual_home(self, toolhead, endstops, pos, speed,
                    triggered, check_triggered):
        hmove = HomingMove(self.printer, endstops, toolhead)
        try:
            hmove.homing_move(pos, speed, triggered=triggered,
                              check_triggered=check_triggered)
        except self.printer.command_error:
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    '{"code": "key4", "msg": "Homing failed due to printer shutdown"}')
            raise
    def probing_move(self, mcu_probe, pos, speed):

        endstops = [(mcu_probe, "probe")]
        hmove = HomingMove(self.printer, endstops)
        try:
            if self.probe_type == "prtouch_v2":
                epos = self.printer.lookup_object('probe').mcu_probe.run_G29_Z()
            else:
                epos = hmove.homing_move(pos, speed, probe_pos=True)
        except self.printer.command_error:
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    '{"code": "key5", "msg": "Probing failed due to printer shutdown"}')
            raise
        # 暂时关闭该检查，跳过误触发导致报错问题，调平代码有异常点重探机制
        # if hmove.check_no_movement() is not None:
        #     raise self.printer.command_error(
        #         '{"code": "key6", "msg": "Probe triggered prior to movement"}')
        return epos
        
    def cmd_STEPPER_Z_SENEORLESS(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        move_dist = gcmd.get_int('MOVE_DIST', default=0, minval=0, maxval=360)
        homing_state = Homing(self.printer)
        homing_state.set_axes([2])
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        gcode = self.printer.lookup_object('gcode')
        gcode.respond_info("cmd_STEPPER_Z_SENEORLESS")
        gcode.respond_info("move_dist {}".format(move_dist))
        kin.home_z_with_sensorless(homing_state, move_dist)
        # gcode = self.printer.lookup_object('gcode')
        pos = toolhead.get_position()
        pos[2] = move_dist - 3
        toolhead.set_position(pos, homing_axes=[2])
        #gcode.run_script_from_command('G4 P1000')
        #gcode.run_script_from_command('G91')
        #gcode.run_script_from_command('G1 Z-5 F2400')
        #gcode.run_script_from_command('M400')
        #gcode.run_script_from_command('G90')
        #toolhead.manual_move([None, None, 0.], 5)
        if hasattr(toolhead.get_kinematics(), "note_z_not_homed"):
            toolhead.get_kinematics().note_z_not_homed()

    def cmd_RECOVERY_Z_ADJUSTMENT(self, gcmd):
        # 调用ZDOWN后再调用此处 恢复z_tilt调整值
        self.resume_adjustment()

    def cmd_G28(self, gcmd):
        # leave flush area
        if self.printer.lookup_object('box').has_flushing_sign():
            self.run_gcmd('LEAVE_FLUSH_AREA')
        # Move to origin
        axes = []
        for pos, axis in enumerate('XYZ'):
            if gcmd.get(axis, None) is not None:
                axes.append(pos)
        if not axes:
            axes = [0, 1, 2]
        homing_state = Homing(self.printer)
        homing_state.set_axes(axes)
        toolhead = self.printer.lookup_object('toolhead')
        kin = toolhead.get_kinematics()
        gcode = self.printer.lookup_object('gcode')
        try:
            if self.probe_type == "prtouch_v2":
                for a in axes:
                    if a == 0 or a == 1:
                        homing_state.set_axes([a])
                        kin.home(homing_state)
                    else:
                        self.printer.lookup_object('probe').mcu_probe.run_G28_Z()
            else:
                homing_state.out_z_all = 0
                for a in axes:
                    if a == 0 or a == 1:
                        homing_state.set_axes([a])
                        kin.home(homing_state)
                    else:
                        if self.config.has_section("z_align"):
                            self.run_gcmd("BED_MESH_CLEAR", wait=True)
                            max_accel = toolhead.get_max_accel()
                            z_align = self.printer.lookup_object('z_align')
                            z_align.force_stop_flag = False
                            gcode.respond_info("is_already_zodwn:%s zdown_switch_enable:%s" % (z_align.is_already_zodwn, z_align.zdown_switch_enable))
                            # 检测到没有做过下光电归位时,强制做一次下光电归位
                            if z_align.is_already_zodwn==False:
                                gcode.run_script_from_command("SET_VELOCITY_LIMIT ACCEL=300")
                                ret = self.run_G28_two_Z()
                                if ret == MOTOR_PROTECT_ERROR:
                                    gcode.respond_info("Z MOTOR_PROTECT_ERROR")
                                    raise
                                gcode.run_script_from_command("SET_VELOCITY_LIMIT ACCEL=%s" % max_accel )
                                kin.home(homing_state)
                                gcode.respond_info("za1:%s za2:%s z_max:%s"%(self.z_move,homing_state.out_z_all,(self.z_move+homing_state.out_z_all)))
                                if toolhead.G29_flag == True:
                                    gcode.respond_info("save config za1:%s za2:%s z_max:%s"%(self.z_move,homing_state.out_z_all,(self.z_move+homing_state.out_z_all)))
                                    self.write_real_zmax(self.z_move+homing_state.out_z_all)
                                z_align.is_already_zodwn = True
                                gcode.run_script_from_command("SET_Z_LIMIT")
                                continue
                            # zdown_switch_enable==1时,强制做光电找平; zdown_switch_enable==0时,不做光电找平
                            if z_align.zdown_switch_enable==1:
                                z_align.zdown_switch_enable = 0
                                gcode.run_script_from_command("SET_VELOCITY_LIMIT ACCEL=300")
                                ret = self.run_G28_two_Z()
                                if ret == MOTOR_PROTECT_ERROR:
                                    gcode.respond_info("Z MOTOR_PROTECT_ERROR")
                                    raise
                                gcode.run_script_from_command("SET_VELOCITY_LIMIT ACCEL=%s" % max_accel )
                                kin.home(homing_state)
                                gcode.respond_info("za1:%s za2:%s z_max:%s"%(self.z_move,homing_state.out_z_all,(self.z_move+homing_state.out_z_all)))
                                if toolhead.G29_flag == True:
                                    gcode.respond_info("save config za1:%s za2:%s z_max:%s"%(self.z_move,homing_state.out_z_all,(self.z_move+homing_state.out_z_all)))
                                    self.write_real_zmax(self.z_move+homing_state.out_z_all)
                                z_align.is_already_zodwn = True
                                gcode.run_script_from_command("SET_Z_LIMIT")
                                continue
                            # 不做光电找平
                            kin.home(homing_state)
                            gcode.run_script_from_command("SET_Z_LIMIT")
                        else:
                            kin.home(homing_state)
        except self.printer.command_error as err:
            logging.exception(err)
            self.set_stall_mode(gcode)
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    "Homing failed due to printer shutdown")
            self.printer.lookup_object('stepper_enable').motor_off()
            raise
        except Exception as err:
            logging.exception(err)
            self.set_stall_mode(gcode)
            raise
    def set_stall_mode(self, gcode, check_protection=True):
        if self.config.has_section("motor_control") and self.config.getsection('motor_control').getint('switch')==1:
            # gcode.run_script_from_command("MOTOR_CLEAR_ERR_WARN_CODE NUM=0 DATA=5") # 清除错误码
            self.printer.lookup_object('motor_control').is_homing = False
            raise
            # gcode.run_script_from_command("MOTOR_STALL_MODE DATA=2") # stall 引脚模式切换为紧急保护输出模式
            # self.printer.get_reactor().pause(self.printer.get_reactor().monotonic() + 1.0)
            # self.printer.lookup_object('motor_control').is_homing = False
            # if check_protection:
            #     gcode.run_script_from_command("MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=10") # 查询电机是否有错误码
    def write_real_zmax(self, data):
        max_z = self.config.getsection('stepper_z').getfloat('position_max', default=360)
        logging.info("stepper_z position_max:%s" % max_z)
        if data < max_z-15 or data > max_z:
            logging.error("real zmax out of range[%s, %s]: %s" % ((max_z-15), max_z, data))
            return
        if self.config.has_section("z_tilt"):
            z_tilt = self.printer.lookup_object('z_tilt')
            with open(z_tilt.real_zmax_path, "w") as f:
                logging.info("real_zmax_path write zmax:%s" % data)
                f.write(json.dumps({"zmax": data}))
                f.flush()

    def run_G28_two_Z(self):
        try:
            # self.move_to_center(speed=50, wait=True)
            gcode = self.printer.lookup_object('gcode')
            gcmd = gcode.create_gcode_command("", "", {})
            z_align = self.printer.lookup_object('z_align')
            ret = z_align.cmd_ZDOWN(gcmd)
            gcode = self.printer.lookup_object('gcode')
            gcode.respond_info("ZDOWN ret:%s"%ret)
            if ret == MOTOR_PROTECT_ERROR:
                return MOTOR_PROTECT_ERROR
            # self.resume_adjustment()
            # self.set_max_z_pos()
            max_z = self.config.getsection('stepper_z').getfloat('position_max')
            # distance_ratio 向上快速运动距离的比例系数
            distance_ratio = self.printer.lookup_object('z_align').distance_ratio
            self.z_move = max_z*distance_ratio
            self.move_z(speed=30, height=self.z_move) 
            # 检测电机保护错误码是否存在
            if self.config.has_section("motor_control") and self.config.getsection('motor_control').getint('switch')==1:
                motor_error_code = self.printer.lookup_object('motor_control').motor_error_code
                for i in range(1,5):
                    if motor_error_code.get(str(i), 0):
                        gcode.respond_info("%s motor_error_code..." % str(i))
                        return MOTOR_PROTECT_ERROR
        except Exception as err:
            logging.exception(err)
        return 0

    def resume_adjustment(self):
        z_tilt = self.printer.lookup_object('z_tilt')
        if os.path.exists(z_tilt.stepper_adjustment_path):
            result = {}
            with open(z_tilt.stepper_adjustment_path, "r") as f:
                try:
                    result = json.loads(f.read())
                except Exception as err:
                    pass
            if result:
                self.move_z(speed=20, height=9)
                stepper_z_adjustment = result.get("stepper_z_adjustment", 0)
                stepper_z1_adjustment = result.get("stepper_z1_adjustment", 0)
                # gcmd_z = "FORCE_MOVE STEPPER=stepper_z DISTANCE=%s VELOCITY=5" % (-stepper_z_adjustment)
                # gcmd_z1 = "FORCE_MOVE STEPPER=stepper_z1 DISTANCE=%s VELOCITY=5" % (-stepper_z1_adjustment)
                gcmd_z = "FORCE_MOVE STEPPER=stepper_z DISTANCE=%s VELOCITY=5" % stepper_z_adjustment # 热床下降 stepper_z_adjustment > 0
                gcmd_z1 = "FORCE_MOVE STEPPER=stepper_z1 DISTANCE=%s VELOCITY=5" % stepper_z1_adjustment
                tolerance = abs(stepper_z1_adjustment) if abs(stepper_z_adjustment) > abs(stepper_z1_adjustment) else abs(stepper_z_adjustment)
                self.run_gcmd(gcmd_z, wait=False)
                self.run_gcmd(gcmd_z1, wait=False)
                # reactor = self.printer.get_reactor()
                # reactor.pause(reactor.monotonic() + 1.0)
                self.move_z(speed=20, height=-9)
                # self.move_z(speed=20, height=-(9-tolerance))

    def run_gcmd(self, gcmd, wait=True):
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        logging.info("run_gcmd:%s"%gcmd)
        gcode.run_script_from_command(gcmd)
        if wait:
            toolhead.wait_moves()

    def move_to_center(self, speed, wait=True):
        toolhead = self.printer.lookup_object('toolhead')
        now_pos = pos = toolhead.get_position()
        min_x = self.config.getsection('stepper_x').getfloat('position_min')
        max_x = self.config.getsection('stepper_x').getfloat('position_max')
        min_y = self.config.getsection('stepper_y').getfloat('position_min')
        max_y = self.config.getsection('stepper_y').getfloat('position_max')
        home_x = min_x + (max_x - min_x) / 2
        home_y = min_y + (max_y - min_y) / 2
        pos[0] = home_x
        pos[1] = home_y
        gcmd = 'G1 F%d X%.3f Y%.3f' % (speed * 60, pos[0], pos[1])
        self.run_gcmd(gcmd, wait=True)

    def move_z(self, speed=50, wait=True, height=5):
        toolhead = self.printer.lookup_object('toolhead')
        now_pos = toolhead.get_position()
        toolhead.set_position(now_pos, homing_axes=(2,))
        logging.info("move_z now_pos:%s"%str(now_pos))
        slow_up_pos = now_pos[2] - 4
        now_pos[2] = now_pos[2] - height
        gcmd = 'G1 F%d X%.3f Y%.3f Z%.3f' % (speed * 60, now_pos[0], now_pos[1], now_pos[2])
        logging.info("move_z gcmd:%s"%gcmd)
        self.run_gcmd("G4 P200", wait=True)
        self.run_gcmd(gcmd, wait=True)

    def set_max_z_pos(self):
        toolhead = self.printer.lookup_object('toolhead')
        now_pos = toolhead.get_position()
        logging.info("before set_max_z_pos cur toolhead.set_position:%s" % str(now_pos))
        now_pos[2] = self.config.getsection('stepper_z').getfloat('position_max')
        toolhead.set_position(now_pos, homing_axes=(2,))
        logging.info("after set_max_z_pos cur toolhead.set_position:%s" % str(now_pos))

    # def check_endstops_z(self):
    #     query_endstops = self.printer.load_object(self.config, 'query_endstops')
    #     # endstops_z = query_endstops.endstops[2][0]
    #     # endstops_z1 = query_endstops.endstops[3][0]
    #     endstops_z_z1 = [query_endstops.endstops[2], query_endstops.endstops[3]]
    #     print_time = self.printer.lookup_object('toolhead').get_last_move_time()
    #     last_state = [(name, mcu_endstop.query_endstop(print_time))
    #                     for mcu_endstop, name in endstops_z_z1]
    #     result = [(name, ["open", "TRIGGERED"][not not t]) for name, t in last_state]
    #     if result[0][1] == "TRIGGERED" or result[1][1] == "TRIGGERED":
    #         return (True, result)
    #     return (False, result)

def load_config(config):
    return PrinterHoming(config)
