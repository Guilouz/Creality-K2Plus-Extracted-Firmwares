# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, logging, io, json, time, re, threading
from .tool import reportInformation
from .base_info import base_dir, system_info_instance

VALID_GCODE_EXTS = ['gcode', 'g', 'gco']
LAYER_KEYS = ["; layer #", ";LAYER:", "; layer:", "; LAYER:", ";AFTER_LAYER_CHANGE", ";LAYER_CHANGE"]

MAINTENANCE_ITEM = {
    "calibrate" : {
        "cut_calibration": {"cur_value": 0},
        "shaper_calibrate": {"cur_value": 0},
        "belt_tensioning": {"cur_value": 0},
        },
    "routine_maintenance" : {
        "motion_mechanism_lubrication": {"cur_value": 0},
        "camera_maintenance": {"cur_value": 0},
        "fan_inspection": {"cur_value": 0}
        },
    "machine_wear_parts_replacement": {
        "nozzle": {"cur_value": 0},
        "cut": {"cur_value": 0},
        "machine_teflon_tube": {"cur_value": 0},
        "wipe_mouth_strip": {"cur_value": 0},
        "air_filter": {"cur_value": 0}
        },
    "cfs_wear_parts_replacement" : {
        "cfs_teflon_tube": {"cur_value": 0},
        "cfs_desiccant": {"cur_value": 0}
    }
}

def capture(end_print=False, frame=15):
    import subprocess
    python_path = "/usr/share/klippy-env/bin/python"
    cmd_path = "/usr/share/klipper/klippy/extras/photograph.py"
    capture_shell = ""
    def run_cmd(capture_shell):
        try:
            logging.info(capture_shell)
            capture_ret = subprocess.check_output(capture_shell, shell=True).decode("utf-8")
            logging.info("%s return:#%s#" % (capture_shell, str(capture_ret)))
        except Exception as err:
            logging.error(err)
    if system_info_instance._h264_encoder_flag == "NO_H264_ENCODER" and end_print == True:
        capture_shell = "capture 0 1"
        background_thread = threading.Thread(target=run_cmd, args=(capture_shell,))
        background_thread.start()
        logging.info("background_thread capture 0 1")
        return
    elif system_info_instance._h264_encoder_flag == "NO_H264_ENCODER" and end_print == False:
        subprocess.Popen([python_path, cmd_path])
    elif system_info_instance._h264_encoder_flag == "H264_ENCODER" and end_print == True:
        interval_time = 1.0 / frame
        start_time = 1
        while start_time > 0:
            run_cmd("capture 0")
            time.sleep(interval_time)
            start_time = start_time - interval_time
    else:
        capture_shell = "capture 0"

    if capture_shell:
        run_cmd(capture_shell)

class VirtualSD:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:shutdown",
                                            self.handle_shutdown)
        self.printer.register_event_handler('klippy:ready',
                self._handle_ready)
        # sdcard state
        sd = config.get('path')
        self.offset_value = config.getfloat('offset_value', 0) # 断电续打偏移补偿值
        self.forced_leveling = config.getboolean('forced_leveling',  default=False)
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        self.current_file = None
        self.file_position = self.file_size = 0
        # Print Stat Tracking
        self.print_stats = self.printer.load_object(config, 'print_stats')
        # Work timer
        self.reactor = self.printer.get_reactor()
        self.must_pause_work = self.cmd_from_sd = False
        self.next_file_position = 0
        self.work_timer = None
        # Error handling
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.on_error_gcode = gcode_macro.load_template(
            config, 'on_error_gcode', '')
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        for cmd in ['M20', 'M21', 'M23', 'M24', 'M25', 'M26', 'M27']:
            self.gcode.register_command(cmd, getattr(self, 'cmd_' + cmd))
        for cmd in ['M28', 'M29', 'M30']:
            self.gcode.register_command(cmd, self.cmd_error)
        self.gcode.register_command(
            "SDCARD_RESET_FILE", self.cmd_SDCARD_RESET_FILE,
            desc=self.cmd_SDCARD_RESET_FILE_help)
        self.gcode.register_command(
            "SDCARD_PRINT_FILE", self.cmd_SDCARD_PRINT_FILE,
            desc=self.cmd_SDCARD_PRINT_FILE_help)
        self.gcode.register_command(
            "SHOW_GCODE_FLUSH", self.cmd_SHOW_GCODE_FLUSH,
            desc=self.cmd_SHOW_GCODE_FLUSH_help)
        self.gcode.register_command("CLEAR_EEPROM_INFO", self.cmd_CLEAR_EEPROM_INFO)
        self.gcode.register_command("SET_MAINTENANCE_ITEM_VARIABLE", self.cmd_SET_MAINTENANCE_ITEM_VARIABLE)
        self.count_G1 = 0 
        self.count_line = 0
        self.do_resume_status = False
        self.eepromWriteCount = 1
        self.fan_state = {}
        self.gcode_layer_path = os.path.join(base_dir, "creality/userdata/config/gcode_layer.json")
        self.user_print_refer_path = os.path.join(base_dir, "creality/userdata/config/user_print_refer.json")
        self.print_file_name_path = os.path.join(base_dir, "creality/userdata/config/print_file_name.json")
        self.speed_mode_path = os.path.join(base_dir, "creality/userdata/config/speed_mode.json")
        self.flow_rate_path = os.path.join(base_dir, "creality/userdata/config/flow_rate.json")
        self.maintenance_item_path = os.path.join(base_dir, "creality/userdata/config/maintenance_item.json")
        self.print_first_layer = False
        self.first_layer_stop = False
        self.count_M204 = 0
        self.layer = 0
        self.layer_count = 0
        self.is_continue_print = False
        self.slow_print = False
        self.slow_count = 0
        self.speed_factor = 1.0/60.0
        self.run_dis = 0.0
        self.print_id = ""
        self.cur_print_data = {}
        self.gcode_metadata = None
        self.end_print_state = False
        self.last_layer = 0
        self.is_cancel = False
        self.bed_mesh_calibate_state = False
        self.run_bed_mesh_calibate = False
        self.layer_key = ""
        self.lock = threading.Lock()
        self.is_move_out_of_range_in_printing = False
    def _handle_ready(self):
        self._maintenance_item_timer = self.reactor.register_timer(self.update_maintenance_item_timer)
        self.reactor.update_timer(self._maintenance_item_timer, self.reactor.NOW)
        self.printer.register_event_handler('v_sd:update_cut_used', self.update_cut_used)
        self.printer.register_event_handler('v_sd:update_filament_used', self.update_filament_used)
        self.printer.register_event_handler('v_sd:cancel_power_loss_update_filament_used', self.cancel_power_loss_update_filament_used)
        self.printer.register_event_handler('v_sd:reset_cut_calibration_count', self.reset_cut_calibration_count)
        self.printer.register_event_handler('v_sd:reset_shaper_calibrate_count', self.reset_shaper_calibrate_count)
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint("get_maintenance_item", self.get_maintenance_item)
    def notify_maintenance_item(self):
        maintenance_item_param = self.printer.lookup_object("gcode_macro MAINTENANCE_ITEM_PARAM", None)
        if maintenance_item_param and self.config.has_section("gcode_macro MAINTENANCE_ITEM") and os.path.exists(self.maintenance_item_path):
            try:
                obj = self.printer.lookup_object("gcode_macro MAINTENANCE_ITEM")
                with open(self.maintenance_item_path, "r") as f:
                    result = json.loads(f.read())
                    result = self.maintenance_item_add_threshold(result)
                    result = self.maintenance_item_add_timeout(result)
                    obj.variables = result
            except Exception as err:
                logging.exception(err)
    def calculate_filament_weight(self, filament_used, filament_diameter=1.75, filament_density=1.25e-3):
        import math
        # 将耗材直径转为半径
        radius = filament_diameter / 2
        # 计算耗材体积 (V = πr^2h)
        volume = math.pi * (radius ** 2) * filament_used
        # 计算耗材重量 体积*密度
        weight = volume * filament_density
        return weight
    def update_cut_used(self):
        self.update_maintenance_item(update_cut_used=True)
    def update_filament_used(self):
        filament_used = self.printer.lookup_object('print_stats').filament_used
        if filament_used > 0:
            weight = self.calculate_filament_weight(filament_used)
            self.update_maintenance_item(update_filament_used=True, filament_used=weight)
    def cancel_power_loss_update_filament_used(self):
        filament_used = 0
        try:
            if os.path.exists(self.print_file_name_path):
                with open(self.print_file_name_path, "r") as f:
                    ret = json.loads(f.read())
                    filament_used = ret.get("filament_used", 0)
            if filament_used > 0:
                weight = self.calculate_filament_weight(filament_used)
                self.update_maintenance_item(update_filament_used=True, filament_used=weight)
        except Exception as err:
            pass
    def reset_cut_calibration_count(self):
        self.gcode.run_script_from_command("SET_MAINTENANCE_ITEM_VARIABLE NAME=calibrate VARIABLE=cut_calibration VALUE=0")
    def reset_shaper_calibrate_count(self):
        self.gcode.run_script_from_command("SET_MAINTENANCE_ITEM_VARIABLE NAME=calibrate VARIABLE=shaper_calibrate VALUE=0")
    def update_maintenance_item_timer(self, eventtime):
        # 通知到订阅端
        self.notify_maintenance_item()
        try:
            if not os.path.exists(self.maintenance_item_path):
                with open(self.maintenance_item_path, 'w') as f:
                    f.write(json.dumps(MAINTENANCE_ITEM))
                    f.flush()
            print_stats = self.printer.lookup_object('print_stats')
            if print_stats.state == "printing":
                self.update_maintenance_item()
        except Exception as e:
            logging.error("Error in update_maintenance_item: %s" % str(e))
        return eventtime + 60.0
    def update_maintenance_item(self, update_cut_used=False, update_filament_used=False, filament_used=0, variable_update=False, update_item_name="", variable_update_obj="", reset_value=0):
        interval = 60
        with self.lock:
            if not os.path.exists(self.maintenance_item_path):
                with open(self.maintenance_item_path, 'w') as f:
                    f.write(json.dumps(MAINTENANCE_ITEM))
                    f.flush()
            else:
                result = {}
                try:
                    with open(self.maintenance_item_path, 'r') as f:
                        result = json.loads(f.read())
                    if not result:
                        os.remove(self.maintenance_item_path)
                        return
                    # 重设记录值
                    if variable_update:
                        if result.get(update_item_name) and result.get(update_item_name).get(variable_update_obj):
                            result[update_item_name][variable_update_obj]["cur_value"] = reset_value
                            with open(self.maintenance_item_path, 'w') as f:
                                f.write(json.dumps(result))
                                f.flush()
                            # 通知到订阅端
                            self.notify_maintenance_item()
                        return
                    # 更新值
                    result = self.check_item(result=result, interval=interval, update_cut_used=update_cut_used, update_filament_used=update_filament_used, filament_used=filament_used)
                    with open(self.maintenance_item_path, 'w') as f:
                        f.write(json.dumps(result))
                        f.flush()
                    # 通知到订阅端
                    self.notify_maintenance_item()
                except Exception as err:
                    logging.error("open maintenance_item_path err:%s" % str(err))
                    os.remove(self.maintenance_item_path)
    def check_item(self, result, interval, update_cut_used, update_filament_used, filament_used=0, max_value=999999999999999):
        if update_cut_used:
            # 更新切刀使用次数
            if result["calibrate"]["cut_calibration"]["cur_value"] < max_value:
                result["calibrate"]["cut_calibration"]["cur_value"] += 1
            if result["machine_wear_parts_replacement"]["cut"]["cur_value"] < max_value:
                result["machine_wear_parts_replacement"]["cut"]["cur_value"] += 1
            return result
        if update_filament_used:
            # 更新耗材使用重量
            if result["machine_wear_parts_replacement"]["nozzle"]["cur_value"] < max_value:
                result["machine_wear_parts_replacement"]["nozzle"]["cur_value"] += filament_used
            return result
        # 更新打印时长
        if result["calibrate"]["shaper_calibrate"]["cur_value"] < max_value:
            result["calibrate"]["shaper_calibrate"]["cur_value"] += interval
        if result["calibrate"]["belt_tensioning"]["cur_value"] < max_value:
            result["calibrate"]["belt_tensioning"]["cur_value"] += interval
        if result["routine_maintenance"]["motion_mechanism_lubrication"]["cur_value"] < max_value:
            result["routine_maintenance"]["motion_mechanism_lubrication"]["cur_value"] += interval
        if result["routine_maintenance"]["camera_maintenance"]["cur_value"] < max_value:
            result["routine_maintenance"]["camera_maintenance"]["cur_value"] += interval
        if result["routine_maintenance"]["fan_inspection"]["cur_value"] < max_value:
            result["routine_maintenance"]["fan_inspection"]["cur_value"] += interval
        if result["machine_wear_parts_replacement"]["machine_teflon_tube"]["cur_value"] < max_value:
            result["machine_wear_parts_replacement"]["machine_teflon_tube"]["cur_value"] += interval
        if result["machine_wear_parts_replacement"]["wipe_mouth_strip"]["cur_value"] < max_value:
            result["machine_wear_parts_replacement"]["wipe_mouth_strip"]["cur_value"] += interval
        if result["machine_wear_parts_replacement"]["air_filter"]["cur_value"] < max_value:
            result["machine_wear_parts_replacement"]["air_filter"]["cur_value"] += interval
        if not self.check_cfs_enable():
            return result
        if result["cfs_wear_parts_replacement"]["cfs_teflon_tube"]["cur_value"] < max_value:
            result["cfs_wear_parts_replacement"]["cfs_teflon_tube"]["cur_value"] += interval
        if result["cfs_wear_parts_replacement"]["cfs_desiccant"]["cur_value"] < max_value:
            result["cfs_wear_parts_replacement"]["cfs_desiccant"]["cur_value"] += interval
        return result
    def check_cfs_enable(self):
        box_enable = 0
        try:
            box = self.printer.lookup_object("box", None)
            if box and os.path.exists(box.box_state.tn_save_data_path):
                with open(box.box_state.tn_save_data_path, "r") as f:
                    data = json.load(f)
                    box_enable = data.get("enable", 0)
        except Exception as err:
            pass
        return box_enable
    def get_maintenance_item(self, web_request):
        response = {}
        maintenance_item_param = self.printer.lookup_object("gcode_macro MAINTENANCE_ITEM_PARAM", None)
        if maintenance_item_param and os.path.exists(self.maintenance_item_path):
            try:
                with open(self.maintenance_item_path, 'r') as f:
                    result = json.loads(f.read())
                    result = self.maintenance_item_add_threshold(result)
                    result = self.maintenance_item_add_timeout(result)
                    response = result
            except Exception as err:
                logging.exception(err)
        web_request.send(response)
        return response
    def maintenance_item_add_threshold(self, result):
        maintenance_item_param = self.printer.lookup_object("gcode_macro MAINTENANCE_ITEM_PARAM", None).variables
        result["calibrate"]["cut_calibration"]["threshold"] = maintenance_item_param.get("cut_calibration")
        result["calibrate"]["shaper_calibrate"]["threshold"] = maintenance_item_param.get("shaper_calibrate")
        result["calibrate"]["belt_tensioning"]["threshold"] = maintenance_item_param.get("belt_tensioning")
        result["routine_maintenance"]["motion_mechanism_lubrication"]["threshold"] = maintenance_item_param.get("motion_mechanism_lubrication")
        result["routine_maintenance"]["camera_maintenance"]["threshold"] = maintenance_item_param.get("camera_maintenance")
        result["routine_maintenance"]["fan_inspection"]["threshold"] = maintenance_item_param.get("fan_inspection")
        result["machine_wear_parts_replacement"]["nozzle"]["threshold"] = maintenance_item_param.get("nozzle")
        result["machine_wear_parts_replacement"]["cut"]["threshold"] = maintenance_item_param.get("cut")
        result["machine_wear_parts_replacement"]["machine_teflon_tube"]["threshold"] = maintenance_item_param.get("machine_teflon_tube")
        result["machine_wear_parts_replacement"]["wipe_mouth_strip"]["threshold"] = maintenance_item_param.get("wipe_mouth_strip")
        result["machine_wear_parts_replacement"]["air_filter"]["threshold"] = maintenance_item_param.get("air_filter")
        result["cfs_wear_parts_replacement"]["cfs_teflon_tube"]["threshold"] = maintenance_item_param.get("cfs_teflon_tube")
        result["cfs_wear_parts_replacement"]["cfs_desiccant"]["threshold"] = maintenance_item_param.get("cfs_desiccant")
        result["calibrate"]["cut_calibration"]["timeout"] = False
        result["calibrate"]["shaper_calibrate"]["timeout"] = False
        result["calibrate"]["belt_tensioning"]["timeout"] = False
        result["routine_maintenance"]["motion_mechanism_lubrication"]["timeout"] = False
        result["routine_maintenance"]["camera_maintenance"]["timeout"] = False
        result["routine_maintenance"]["fan_inspection"]["timeout"] = False
        result["machine_wear_parts_replacement"]["nozzle"]["timeout"] = False
        result["machine_wear_parts_replacement"]["cut"]["timeout"] = False
        result["machine_wear_parts_replacement"]["machine_teflon_tube"]["timeout"] = False
        result["machine_wear_parts_replacement"]["wipe_mouth_strip"]["timeout"] = False
        result["machine_wear_parts_replacement"]["air_filter"]["timeout"] = False
        result["cfs_wear_parts_replacement"]["cfs_teflon_tube"]["timeout"] = False
        result["cfs_wear_parts_replacement"]["cfs_desiccant"]["timeout"] = False
        return result
    def maintenance_item_add_timeout(self, result):
        maintenance_item_param = self.printer.lookup_object("gcode_macro MAINTENANCE_ITEM_PARAM", None).variables
        if result["calibrate"]["cut_calibration"]["cur_value"] > maintenance_item_param.get("cut_calibration"):
            result["calibrate"]["cut_calibration"]["timeout"] = True
        if result["calibrate"]["shaper_calibrate"]["cur_value"] > maintenance_item_param.get("shaper_calibrate"):
            result["calibrate"]["shaper_calibrate"]["timeout"] = True
        if result["calibrate"]["belt_tensioning"]["cur_value"] > maintenance_item_param.get("belt_tensioning"):
            result["calibrate"]["belt_tensioning"]["timeout"] = True
        if result["routine_maintenance"]["motion_mechanism_lubrication"]["cur_value"] > maintenance_item_param.get("motion_mechanism_lubrication"):
            result["routine_maintenance"]["motion_mechanism_lubrication"]["timeout"] = True
        if result["routine_maintenance"]["camera_maintenance"]["cur_value"] > maintenance_item_param.get("camera_maintenance"):
            result["routine_maintenance"]["camera_maintenance"]["timeout"] = True
        if result["routine_maintenance"]["fan_inspection"]["cur_value"] > maintenance_item_param.get("fan_inspection"):
            result["routine_maintenance"]["fan_inspection"]["timeout"] = True
        if result["machine_wear_parts_replacement"]["nozzle"]["cur_value"] > maintenance_item_param.get("nozzle"):
            result["machine_wear_parts_replacement"]["nozzle"]["timeout"] = True
        if result["machine_wear_parts_replacement"]["cut"]["cur_value"] > maintenance_item_param.get("cut"):
            result["machine_wear_parts_replacement"]["cut"]["timeout"] = True
        if result["machine_wear_parts_replacement"]["machine_teflon_tube"]["cur_value"] > maintenance_item_param.get("machine_teflon_tube"):
            result["machine_wear_parts_replacement"]["machine_teflon_tube"]["timeout"] = True
        if result["machine_wear_parts_replacement"]["wipe_mouth_strip"]["cur_value"] > maintenance_item_param.get("wipe_mouth_strip"):
            result["machine_wear_parts_replacement"]["wipe_mouth_strip"]["timeout"] = True
        if result["machine_wear_parts_replacement"]["air_filter"]["cur_value"] > maintenance_item_param.get("air_filter"):
            result["machine_wear_parts_replacement"]["air_filter"]["timeout"] = True
        if result["cfs_wear_parts_replacement"]["cfs_teflon_tube"]["cur_value"] > maintenance_item_param.get("cfs_teflon_tube"):
            result["cfs_wear_parts_replacement"]["cfs_teflon_tube"]["timeout"] = True
        if result["cfs_wear_parts_replacement"]["cfs_desiccant"]["cur_value"] > maintenance_item_param.get("cfs_desiccant"):
            result["cfs_wear_parts_replacement"]["cfs_desiccant"]["timeout"] = True
        return result
    def cmd_SET_MAINTENANCE_ITEM_VARIABLE(self, gcmd):
        # SET_MAINTENANCE_ITEM_VARIABLE NAME=calibrate VARIABLE=cut_calibration VALUE=0
        name = gcmd.get("NAME", "")
        variable = gcmd.get("VARIABLE", "")
        value = gcmd.get_int("VALUE", 0)
        if name and variable:
            self.update_maintenance_item(variable_update=True, update_item_name=name, variable_update_obj=variable, reset_value=value)
    def handle_shutdown(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.current_file.seek(readpos)
                data = self.current_file.read(readcount + 128)
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info("Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                         readpos, repr(data[:readcount]),
                         self.file_position, repr(data[readcount:]))
        self.print_first_layer = False
        self.first_layer_stop = False
        self.print_stats.power_loss = 0
        self.count_M204 = 0
        self.fan_state = {}
    def stats(self, eventtime):
        if self.work_timer is None:
            return False, ""
        return True, "sd_pos=%d" % (self.file_position,)
    def get_file_list(self, check_subdirs=False):
        if check_subdirs:
            flist = []
            for root, dirs, files in os.walk(
                    self.sdcard_dirname, followlinks=True):
                for name in files:
                    ext = name[name.rfind('.')+1:]
                    if ext not in VALID_GCODE_EXTS:
                        continue
                    full_path = os.path.join(root, name)
                    r_path = full_path[len(self.sdcard_dirname) + 1:]
                    size = os.path.getsize(full_path)
                    flist.append((r_path, size))
            return sorted(flist, key=lambda f: f[0].lower())
        else:
            dname = self.sdcard_dirname
            try:
                filenames = os.listdir(self.sdcard_dirname)
                return [(fname, os.path.getsize(os.path.join(dname, fname)))
                        for fname in sorted(filenames, key=str.lower)
                        if not fname.startswith('.')
                        and os.path.isfile((os.path.join(dname, fname)))]
            except:
                logging.exception("virtual_sdcard get_file_list")
                raise self.gcode.error("Unable to get file list")
    def get_status(self, eventtime):
        return {
            'file_path': self.file_path(),
            'progress': self.progress(),
            'is_active': self.is_active(),
            'file_position': self.file_position,
            'file_size': self.file_size,
            'first_layer_stop':  self.first_layer_stop,
            'layer': self.layer,
            'layer_count': self.layer_count,
            'run_dis': self.run_dis,
            'bed_mesh_calibate_state': self.bed_mesh_calibate_state
        }
    def file_path(self):
        if self.current_file:
            return self.current_file.name
        return None
    def progress(self):
        if self.file_size:
            return float(self.file_position) / self.file_size
        else:
            return 0.
    def is_active(self):
        return self.work_timer is not None
    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + .001)
    def do_resume(self):
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        self.must_pause_work = False
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW)
    def do_cancel(self):
        self.printer.send_event("v_sd:update_filament_used")
        self.reactor.pause(self.reactor.monotonic() + 0.2)
        self.is_cancel = True
        self.print_stats.power_loss = 0
        self.first_layer_stop = False
        self.print_first_layer = False
        self.count_M204 = 0
        self.layer = 0
        self.layer_count = 0
        self.fan_state = {}
        self.resume_print_speed()
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
            self.print_stats.note_cancel()
        self.is_cancel = False
        self.file_position = self.file_size = 0.

    def cmd_CLEAR_EEPROM_INFO(self, gcmd):
        from subprocess import call
        if os.path.exists(self.print_file_name_path):
            os.remove(self.print_file_name_path)
        if os.path.exists(self.gcode.exclude_object_info):
            os.remove(self.gcode.exclude_object_info)
        call("sync", shell=True)
        try:
            power_loss_switch = False
            if os.path.exists(self.user_print_refer_path):
                with open(self.user_print_refer_path, "r") as f:
                    data = json.loads(f.read())
                    power_loss_switch = data.get("power_loss", {}).get("switch", False)
            bl24c16f = self.printer.lookup_object('bl24c16f') if "bl24c16f" in self.printer.objects else None
            if power_loss_switch and bl24c16f:
                bl24c16f.setEepromDisable()
                # self.gcode.run_script("EEPROM_WRITE_BYTE ADDR=1 VAL=255")
        except Exception as err:
            logging.error(err)
        self.update_print_history_info(only_update_status=True, state="cancelled")
        if self.print_id and self.cur_print_data:
            reportInformation("key701", data=self.cur_print_data)
            self.print_id = ""
            self.cur_print_data = {}

    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error("SD write not supported")
    def _reset_file(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
        self.file_position = self.file_size = 0.
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")
    cmd_SDCARD_RESET_FILE_help = "Clears a loaded SD File. Stops the print "\
        "if necessary"
    def cmd_SDCARD_RESET_FILE(self, gcmd):
        if self.cmd_from_sd:
            raise gcmd.error(
                "SDCARD_RESET_FILE cannot be run from the sdcard")
        self._reset_file()
    cmd_SDCARD_PRINT_FILE_help = "Loads a SD file and starts the print.  May "\
        "include files in subdirectories."
    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        if self.config.has_section("motor_control") and self.config.getsection('motor_control').getint('switch')==1:
            if self.printer.lookup_object('motor_control').is_ready == False:
                self.gcode.respond_info("The motor parameters are initializing, Please try again later...")
                return
        if self.config.has_section("prtouch_v3") and self.bed_mesh_calibate_state == False and gcmd.get("ISCONTINUEPRINT", False) == False and self.forced_leveling:
            self.run_bed_mesh_calibate = True
        self.end_print_state = False
        self.layer_key = ""
        self.print_id = ""
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get("FILENAME")
        self.is_continue_print = gcmd.get("ISCONTINUEPRINT", False)
        self.printer.lookup_object("box").box_state.is_continue_print = gcmd.get("ISCONTINUEPRINT", False)
        self.rm_power_loss_info()
        first_floor = gcmd.get("FIRST_FLOOR_PRINT", None)
        if first_floor is None or first_floor == False:
            self.print_first_layer = False
        else:
            self.print_first_layer = True
        if filename[0] == '/':
            filename = filename[1:]
        self._load_file(gcmd, filename, check_subdirs=True)
        self.load_gcode_metadata(str(self.current_file.name))
        self.record_print_history(str(self.current_file.name))
        self.do_resume()
    cmd_SHOW_GCODE_FLUSH_help = "Load SD file and display multi-color gcode material change flushing parameters."
    def cmd_SHOW_GCODE_FLUSH(self, gcmd):
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        filename = gcmd.get("FILENAME")
        if filename is None:
            logging.warning('Invalid FILENAME parameter')
            return
        if filename[0] == '/':
            filename = filename[1:]
        self.load_gcode_metadata(str(filename))
        flush_para = self.get_gcode_flush_para()
        if flush_para is None:
            logging.warning('Error in getting flushing parameters')
            return
        self.gcode.respond_info("shwo gcode flush para: %s" % (flush_para))

    def load_gcode_metadata(self, file_path=""):
        self.gcode_metadata = self.get_print_file_metadata(file_path)
        logging.info("gcode_metadata: %s" % (self.gcode_metadata))

    def record_print_history(self, file_path=""):
        try:
            if os.path.exists(file_path):
                dir_path = os.path.dirname(file_path)
                file_name = os.path.basename(file_path)
                metadata_info = self.get_print_file_metadata(filename=file_name, filepath=dir_path)
                self.layer_count = self.get_file_layer_count(self.current_file.name, metadata_info=metadata_info)
                start_time = time.time()
                self.print_id = str(start_time)
                metadata = metadata_info.get("metadata", {})
                # Give print_id to AI Engine
                json_to_write = {"print_id": self.print_id}
                with open('/tmp/cx_print_id.json', 'w') as f:
                    json.dump(json_to_write, f)
                    f.flush()
                data = {
                    "end_time": start_time,
                    "filament_used": 0,
                    "filename": file_name,
                    "metadata": metadata,
                    "print_duration": 0,
                    "start_time": start_time,
                    "status": "in_progress",
                    "total_duration": 0,
                }
                result = {"count": 1, "jobs": [data]}
                self.cur_print_data = result
                return
        except Exception as err:
            logging.error(err)

    def update_print_history_info(self, only_update_status=False, state="", error_msg=""):
        if self.print_id:
            ret = {}
            try:
                update_obj = None
                index = -1
                ret = self.cur_print_data
                if ret and ret.get("jobs", []):
                    print_list = ret.get("jobs", [])
                    for obj in print_list:
                        if obj.get("start_time", "") and str(obj.get("start_time", "")) == self.print_id:
                            index = print_list.index(obj)
                            update_obj = obj
                            if not only_update_status:
                                update_obj["filament_used"] = self.print_stats.filament_used
                                update_obj["print_duration"] = self.print_stats.print_duration
                                update_obj["total_duration"] = self.print_stats.total_duration
                            update_obj["end_time"] = time.time()
                            if not state:
                                state = "in_progress"
                            if error_msg:
                                update_obj["error_msg"] = error_msg
                            update_obj["status"] = state

                if index != -1:
                    print_list[index] = update_obj
                    ret["jobs"] = print_list
                    self.cur_print_data = ret
            except Exception as err:
                logging.error(err)

    def rm_power_loss_info(self):
        if not self.is_continue_print and os.path.exists(self.print_file_name_path):
            try:
                power_loss_switch = False
                with open(self.user_print_refer_path, "r") as f:
                    data = json.loads(f.read())
                    power_loss_switch = data.get("power_loss", {}).get("switch", False)
                bl24c16f = self.printer.lookup_object('bl24c16f') if "bl24c16f" in self.printer.objects and power_loss_switch else None
                if power_loss_switch and bl24c16f:
                    os.remove(self.print_file_name_path)
                    if os.path.exists(self.gcode.exclude_object_info):
                        os.remove(self.gcode.exclude_object_info)
                    self.gcode.run_script_from_command("EEPROM_WRITE_BYTE ADDR=1 VAL=255")
                    logging.info("rm power_loss info success")
            except Exception as err:
                logging.error("rm power_loss info fail, err:%s" % err)
    def cmd_M20(self, gcmd):
        # List SD card
        files = self.get_file_list()
        gcmd.respond_raw("Begin file list")
        for fname, fsize in files:
            gcmd.respond_raw("%s %d" % (fname, fsize))
        gcmd.respond_raw("End file list")
    def cmd_M21(self, gcmd):
        # Initialize SD card
        gcmd.respond_raw("SD card ok")
    def cmd_M23(self, gcmd):
        # Select SD file
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get_raw_command_parameters().strip()
        if filename.startswith('/'):
            filename = filename[1:]
        self._load_file(gcmd, filename)
    def _load_file(self, gcmd, filename, check_subdirs=False):
        files = self.get_file_list(check_subdirs)
        flist = [f[0] for f in files]
        files_by_lower = { fname.lower(): fname for fname, fsize in files }
        fname = filename
        try:
            if fname not in flist:
                fname = files_by_lower[fname.lower()]
            fname = os.path.join(self.sdcard_dirname, fname)
            f = io.open(fname, 'r', newline='')
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise gcmd.error("""{"code":"key121", "msg": "Unable to open file", "values": []}""")
        gcmd.respond_raw("File opened:%s Size:%d" % (filename, fsize))
        gcmd.respond_raw("File selected")
        self.current_file = f
        self.file_position = 0
        self.file_size = fsize
        self.print_stats.set_current_file(filename)
    def cmd_M24(self, gcmd):
        # Start/resume SD print
        self.do_resume()
    def cmd_M25(self, gcmd):
        # Pause SD print
        self.do_pause()
    def cmd_M26(self, gcmd):
        # Set SD position
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        pos = gcmd.get_int('S', minval=0)
        self.file_position = pos
    def cmd_M27(self, gcmd):
        # Report SD print status
        if self.current_file is None:
            gcmd.respond_raw("Not SD printing.")
            return
        gcmd.respond_raw("SD printing byte %d/%d"
                         % (self.file_position, self.file_size))
    def get_file_position(self):
        return self.next_file_position
    def set_file_position(self, pos):
        self.next_file_position = pos
    def is_cmd_from_sd(self):
        return self.cmd_from_sd
    def tail_read(self, f):
        cur_pos = f.tell()
        buf = ''
        while True:
            try:
                b = str(f.read(1))
            except UnicodeDecodeError as err:
                logging.error("UnicodeDecodeError err:%s" % str(err))
                cur_pos -= 1
                if cur_pos < 0: break
                f.seek(cur_pos)
                continue
            buf = b + buf
            cur_pos -= 1
            if cur_pos < 0: break
            f.seek(cur_pos)
            if b.startswith("\n") or b.startswith("\r"):
                buf = '\n'
            if (buf.startswith("G1") or buf.startswith("G0")) and buf.endswith("\n"):
                if ";" in buf:
                    buf = buf.split(";")[0]+"\n"
                break
        return buf
    def check_Tn(self, file_path):
        # 判断是否为多色文件
        READ_SIZE = 512 * 1024
        header_data = ""
        # result 0为单色 1为多色中的单色(只有一个T0) 2为多色(有T0、T1、T2等)
        result = 0
        count = 5
        with open(file_path, 'r') as f:
            while count > 0:
                count -= 1
                header_data = f.read(int(READ_SIZE))
                pattern_T = r"(?m)^\s*T(\d+)\s*$"
                pattern_G = r"(?m)^\s*G1"
                value_G = re.findall(pattern_G, header_data)
                # 匹配到G1指令的时候, 再去匹配T指令
                if len(value_G)<=1:
                    continue
                value_T = re.findall(pattern_T, header_data)
                if len(value_T) == 1:
                    result = 1
                elif len(value_T) > 1:
                    result = 2
                break
        return result

    def getXYZET(self, file_path, file_position):
        # Tn能拿到值的话 证明是多色文件 需要遍历到T值再退出循环
        Tn = self.check_Tn(file_path)
        result = {"X": 0, "Y": 0, "Z": 0, "E": 0, "T": ""}
        try:
            import io
            with io.open(file_path, "r", encoding="utf-8") as f:
                f.seek(file_position)
                while True:
                    cur_pos = f.tell()
                    if cur_pos<=0:
                        break
                    line = self.tail_read(f)
                    line_list = line.split(" ")
                    if not result["E"] and "E" in line:
                        for obj in line_list:
                            if obj.startswith("E"):
                                ret = obj[1:].split("\r")[0]
                                ret = ret.split("\n")[0]
                                if ret.startswith("."):
                                    result["E"] = float(("0"+ret.strip(" ")))
                                else:
                                    result["E"] = float(ret.strip(" "))
                    if not result["X"] and not result["Y"] and "X" in line and "Y" in line:
                        for obj in line_list:
                            if obj.startswith("X"):
                                logging.info("power_loss getXYZET X:%s" % obj)
                                result["X"] = float(obj.split("\r")[0][1:])
                            if obj.startswith("Y"):
                                logging.info("power_loss getXYZET Y:%s" % obj)
                                result["Y"] = float(obj.split("\r")[0][1:])
                    if not result["Z"] and "Z" in line:
                        for obj in line_list:
                            if obj.startswith("Z"):
                                logging.info("power_loss getXYZET Z:%s" % obj)
                                result["Z"] = float(obj.split("\r")[0][1:])
                    if result["X"] and result["Y"] and result["Z"] and result["E"]:
                        break
                    self.reactor.pause(self.reactor.monotonic() + .001)
            if Tn == 1:
                result["T"] = "T0"
                logging.info("power_loss get XYZET T:%s" % str(result))
            elif Tn == 2:
                # 获取file_postion的位置的上一个Tn值
                READ_SIZE = 512*1024
                pattern = r"(?m)^\s*T(\d+)\s*$"
                with io.open(file_path, "r", encoding="utf-8") as f:
                    while file_position > 0:
                        pos = max(file_position - READ_SIZE, 0)
                        read_size = min(READ_SIZE, file_position)
                        f.seek(pos)
                        header_data = f.read(read_size)
                        values = re.findall(pattern, header_data)
                        if values:
                            result["T"] = "T%s"%values[-1]
                            logging.info("power_loss get XYZET T:%s" % str(result))
                            break
                        file_position = pos
                        if pos == 0:
                            logging.info("read the file without finding a match")
                            break
                        self.reactor.pause(self.reactor.monotonic() + .001)
            logging.info("power_loss get XYZET:%s" % str(result))
        except UnicodeDecodeError as err:
            logging.exception(err)
            # UnicodeDecodeError 'utf-8' codec can't decode byte 0xff in postion 5278: invalid start byte
            err_msg = '{"code": "key572", "msg": "File UnicodeDecodeError"}'
            self.gcode.respond_info(err_msg)
            raise self.printer.command_error(err_msg)
        except Exception as err:
            logging.exception(err)
        return result
    def get_print_temperature(self, file_path):
        bed = 50
        extruder = 202.0
        chamber_heater = 0
        if os.path.exists(self.gcode.last_temperature_info):
            try:
                with open(self.gcode.last_temperature_info, "r") as f:
                    result = f.read()
                    if len(result) > 0:
                        result = json.loads(result)
                        bed = float(result.get("bed", 0))
                        extruder = float(result.get("extruder", 201.0))
                        chamber_heater = float(result.get("chamber_heater", 0))
            except Exception as err:
                logging.error("get_print_temperature: %s" % err)
        logging.info("power_loss get_print_temperature: bed:%s, extruder:%s, chamber_heater:%s" % (bed, extruder, chamber_heater))
        return bed, extruder, chamber_heater

    def record_layer(self, layer):
        """
        record current print file layer
        """
        with open(self.gcode_layer_path, "w") as f:
            f.write(json.dumps({"layer": layer}))
            f.flush()
            
    def get_layer(self):
        """
        get last print file layer
        """
        layer = 0
        if os.path.exists(self.gcode_layer_path):
            try:
                with open(self.gcode_layer_path, "r") as f:
                    layer = int(json.loads(f.read()).get("layer"))
            except Exception as err:
                logging.error(err)
                os.remove(self.gcode_layer_path)
        return layer

    def get_print_file_metadata(self, filename, filepath=""):
        from subprocess import check_output
        if not filepath:
            filepath = os.path.join(base_dir, "printer_data/gcodes")
        result = {}
        python_env = "/usr/share/klippy-env/bin/python3"
        # -f gcode filename  -p gcode file dir
        cmd = "%s /usr/share/klipper/klippy/extras/metadata.py -f '%s' -p %s" % (python_env, filename, filepath)
        try:
            result = json.loads(check_output(cmd, shell=True).decode("utf-8"))
        except Exception as err:
            logging.error(err)
        return result
    
    def get_file_layer_count(self, filename, metadata_info=None):
        filename = filename.split("/")[-1]
        import math
        layer_count = 0
        if metadata_info:
            result = metadata_info
        else:
            result = self.get_print_file_metadata(filename, get_layer_count=True)
        if not result:
            return layer_count
        try:
            layer_count = result.get("metadata").get("layer_count", 0)
            first_layer_height = result.get("metadata").get("first_layer_height", 0)
            object_height = result.get("metadata").get("object_height", 0)
            layer_height = result.get("metadata").get("layer_height", 0)
            if not layer_count and object_height > 0 and layer_height > 0:
                layer_count = math.ceil((object_height - first_layer_height) / layer_height + 1)
        except Exception as err:
            logging.error(err)
        return layer_count
    
    def get_gcode_flush_para(self):
        flush_para = None
        try:
            flush_para = self.gcode_metadata.get("metadata").get("flush_para", None)
        except Exception as err:
            logging.error(err)
        return flush_para
        
    def resume_print_speed(self):
        if self.slow_print == True:
            self.slow_print = False
            self.slow_count = 0
            try:
                speed_mode_path = self.speed_mode_path
                speed_mode = -1
                value = -1
                if os.path.exists(speed_mode_path):
                    with open(speed_mode_path, "r") as f:
                        result = json.loads(f.read())
                        speed_mode = result.get("speed_mode", -1)
                        value = result.get("value", -1)
                if speed_mode != -1:
                    speed_cmd = ""
                    if speed_mode == 1 and value!= -1:
                        speed_cmd = "M220 S%s" % value
                    elif speed_mode == 2:
                        speed_cmd = "Qmode"
                    if speed_cmd:
                        self.gcode.run_script_from_command(speed_cmd)
                        self.gcode.run_script_from_command("M400")
                        logging.info("power_loss slow_print speed_mode:%s Resume" % speed_cmd)
            except Exception as err:
                logging.error("resume_print_speed err:%s" % err)
            self.resume_flow_rate()

    def resume_flow_rate(self):
        try:
            value = -1
            if os.path.exists(self.flow_rate_path):
                with open(self.flow_rate_path, "r") as f:
                    result = json.loads(f.read())
                    value = result.get("value", -1)
            speed_cmd = ""
            
            if value != -1:
                speed_cmd = "M221 S%s" % value
            if speed_cmd:
                self.gcode.run_script_from_command(speed_cmd)
                self.gcode.run_script_from_command("M400")
                logging.info("power_loss slow_print resume_flow_rate:%s Resume" % speed_cmd)
        except Exception as err:
            logging.error("resume_flow_rate err:%s" % err)

    def get_delay_photography_info(self):
        delay_photography_switch = 1
        location = 0
        frame = 15
        interval = 1
        power_loss_switch = False
        try:
            if os.path.exists(self.user_print_refer_path):
                with open(self.user_print_refer_path, "r") as f:
                    data = json.loads(f.read())
                    delay_photography_switch = data.get("delay_image", {}).get("switch", 1)
                    location = data.get("delay_image", {}).get("location", 0)
                    frame = data.get("delay_image", {}).get("frame", 15)
                    interval = data.get("delay_image", {}).get("interval", 1)
                    power_loss_switch = data.get("power_loss", {}).get("switch", False)
        except Exception as err:
            logging.error(err)
        return delay_photography_switch, location, frame, interval, power_loss_switch

    def restore_print(self, gcode_move, power_loss_switch, bl24c16f, eepromState):
        sameFileName = False
        if self.is_continue_print and os.path.exists(self.print_file_name_path):
            with open(self.print_file_name_path, "r") as f:
                result = (json.loads(f.read()))
                if result.get("file_path", "") == self.current_file.name:
                    sameFileName = True
                else:
                    # clear power_loss info
                    os.remove(self.print_file_name_path)
                    if os.path.exists(self.gcode.exclude_object_info):
                        os.remove(self.gcode.exclude_object_info)
                    if power_loss_switch and bl24c16f:
                        bl24c16f.setEepromDisable()
        if power_loss_switch and self.is_continue_print and not self.do_resume_status and sameFileName and bl24c16f:
            eepromState = bl24c16f.checkEepromFirstEnable() if power_loss_switch and bl24c16f else True
            if not eepromState:
                with self.gcode.mutex:
                    try:
                        self.print_stats.note_start(info_path=self.print_file_name_path)
                        self.is_continue_print = False
                        logging.info("power_loss start do_resume...")
                        logging.info("power_loss start print, filename:%s" % self.current_file.name)
                        pos = bl24c16f.eepromReadHeader()
                        logging.info("power_loss pos:%s" % pos)
                        print_info = bl24c16f.eepromReadBody(pos)
                        logging.info("power_loss print_info:%s" % str(print_info))
                        self.file_position = int(print_info.get("file_position", 0))
                        logging.info("power_loss file_position:%s" % self.file_position)
                        self.layer = self.get_layer()
                        gcode = self.printer.lookup_object('gcode')
                        temperature = self.get_print_temperature(self.current_file.name)
                        gcode.run_script_from_command("M140 S%s" % temperature[0])
                        gcode.run_script_from_command("M109 S%s" % temperature[1])
                        gcode.run_script_from_command("M141 S%s" % temperature[2]) if temperature[2] > 0 else None
                        XYZET = self.getXYZET(self.current_file.name, self.file_position)
                        logging.info("power_loss XYZET:%s, file_position:%s  " % (str(XYZET), self.file_position))
                        if XYZET.get("Z") == 0:
                            logging.error("power_loss gcode Z == 0 err")
                            from subprocess import call
                            if os.path.exists(self.print_file_name_path):
                                os.remove(self.print_file_name_path)
                            if os.path.exists(self.gcode.exclude_object_info):
                                os.remove(self.gcode.exclude_object_info)
                            call("sync", shell=True)
                            try:
                                power_loss_switch = False
                                if os.path.exists(self.user_print_refer_path):
                                    with open(self.user_print_refer_path, "r") as f:
                                        data = json.loads(f.read())
                                        power_loss_switch = data.get("power_loss", {}).get("switch", False)
                                bl24c16f = self.printer.lookup_object('bl24c16f') if "bl24c16f" in self.printer.objects else None
                                if power_loss_switch and bl24c16f:
                                    bl24c16f.setEepromDisable()
                            except Exception as err:
                                logging.error("power_loss gcode Z == 0: %s" % err)
                            error_message = "power_loss gcode Z == 0, stop print"
                            self.print_stats.note_error(error_message)
                            raise
                        gcode_move.cmd_CX_RESTORE_GCODE_STATE(print_info, self.print_file_name_path, XYZET)
                        logging.info("power_loss end do_resume success")
                        self.print_stats.power_loss = 0
                        # 此处为设置慢速打印, 多色打印时不设置慢速打印
                        if self.layer > 1 and XYZET.get("T") == "":
                            self.slow_print = True
                            self.slow_count = self.layer + 1
                            self.speed_factor = gcode_move.speed_factor
                            self.gcode.run_script_from_command("M220 S20")
                            logging.info("power_loss slow_print M220 S20 SET")
                        self.gcode.run_script_from_command("SET_PIN PIN=extruder_fan VALUE=1")
                    except Exception as err:
                        self.print_stats.power_loss = 0
                        logging.error(err)
            else:
                self.gcode.run_script("G90")
        else:
            self.gcode.run_script("G90")
        return eepromState

    def record_power_loss_info(self,power_loss_switch, bl24c16f,eepromState, gcode_move, start_time, end_time, interval_start_time, interval_end_time):
        try:
            # 置一个标志位 有层信息的判断层信息,一层写一次EEPROM, 把file_postion写到里面
            # 没有层信息的话 5秒写一次EEPROM
            state = False
            if self.layer > 2 and self.layer>self.last_layer and gcode_move.last_position[2] > 1.0:
                state = True
            elif self.layer == 0 and gcode_move.last_position[2] > 1.0:
                if end_time-start_time>5:
                    state = True
            # if power_loss_switch and bl24c16f and (self.layer > 2 or (self.count_G1 > 18 and gcode_move.last_position[2] > 0.6)) and end_time-start_time>5 and self.file_position>0:
            if power_loss_switch and bl24c16f and (self.layer > 6 or (self.count_G1 > 18 and gcode_move.last_position[2] > 1.0)) and state and self.file_position>0:
                logging.info("record_power_loss_info to eeprom layer:%s last_position[2]:%s" % (self.layer, gcode_move.last_position[2]))
                self.last_layer = self.layer
                start_time = end_time
                base_position_e = round(list(gcode_move.base_position)[-1], 2)
                pos = bl24c16f.eepromReadHeader()
                if eepromState:
                    # eeprom first enable
                    self.gcode.run_script("EEPROM_WRITE_BYTE ADDR=1 VAL=1")
                    self.gcode.run_script("EEPROM_WRITE_INT ADDR=%s VAL=%s" % (pos*8, int(self.file_position)))
                    self.gcode.run_script("EEPROM_WRITE_FLOAT ADDR=%s VAL=%s" % (pos*8+4, base_position_e))
                    self.gcode.run_script("EEPROM_WRITE_BYTE ADDR=0 VAL=%d" % pos)
                    eepromState = False
                else:
                    # pos = bl24c16f.eepromReadHeader()
                    if self.eepromWriteCount < 256:
                        self.gcode.run_script("EEPROM_WRITE_INT ADDR=%s VAL=%s" % (pos*8, int(self.file_position)))
                        self.gcode.run_script("EEPROM_WRITE_FLOAT ADDR=%s VAL=%s" % (pos*8+4, base_position_e))
                    else:
                        self.eepromWriteCount = 1
                        pos += 1
                        if pos == 256:
                            pos = 1
                        self.gcode.run_script("EEPROM_WRITE_INT ADDR=%s VAL=%s" % (pos*8, int(self.file_position)))
                        self.gcode.run_script("EEPROM_WRITE_FLOAT ADDR=%s VAL=%s" % (pos*8+4, base_position_e))
                        self.gcode.run_script("EEPROM_WRITE_BYTE ADDR=0 VAL=%d" % pos)
                    # logging.info("eepromWriteCount:%d, pos:%d" % (self.eepromWriteCount, pos))
                self.eepromWriteCount += 1
        except Exception as err:
            logging.error("EEPROM_WRITE ERROR:%s" % str(err))
        
        if power_loss_switch and bl24c16f and self.count_G1 == 19:
            gcode_move.recordPrintFileName(self.print_file_name_path, self.current_file.name, fan_state=self.fan_state, filament_used=self.print_stats.filament_used, last_print_duration=self.print_stats.print_duration)
        # if power_loss_switch and bl24c16f and (self.layer > 2 or gcode_move.last_position[2] > 3) and self.count_line % 999 == 0:
        if power_loss_switch and bl24c16f and (self.layer > 6 or gcode_move.last_position[2] > 3) and self.current_file and interval_end_time-interval_start_time > 15:
            interval_start_time = interval_end_time
            gcode_move.recordPrintFileName(self.print_file_name_path, self.current_file.name, fan_state=self.fan_state, filament_used=self.print_stats.filament_used, last_print_duration=self.print_stats.print_duration)
        return start_time, end_time, interval_start_time, interval_end_time

    def judge_line_starts_with(self, line, power_loss_switch, bl24c16f):
        if not power_loss_switch:
            return line
        if line.startswith("M106"):
            M106_line = line.strip("\r").strip("\n")
            if M106_line.startswith("M106 S"):
                self.fan_state["M106 S"] = M106_line
            elif M106_line.startswith("M106 P0"):
                self.fan_state["M106 P0"] = M106_line
            elif M106_line.startswith("M106 P1"):
                self.fan_state["M106 P1"] = M106_line
            elif M106_line.startswith("M106 P2"):
                self.fan_state["M106 P2"] = M106_line
        elif line.startswith("END_PRINT"):
            self.end_print_state = True
            if self.print_id and os.path.exists("/tmp/camera_main"):
                reportInformation("key608", data={"print_id": self.print_id})
            if os.path.exists(self.print_file_name_path):
                os.remove(self.print_file_name_path)
            if os.path.exists(self.gcode.exclude_object_info):
                os.remove(self.gcode.exclude_object_info)
            if power_loss_switch and bl24c16f:
                self.gcode.run_script("EEPROM_WRITE_BYTE ADDR=1 VAL=255")
        elif line.startswith("M600"):
            line = "PAUSE"
        return line

    def record_layer_info(self, line, power_loss_switch):
        if not power_loss_switch:
            return
        if line.startswith(";"):
            if self.layer_key and line.startswith(self.layer_key):
                self.layer += 1
                self.record_layer(self.layer)
                self.reactor.pause(self.reactor.monotonic() + 0.001)
                return
            for layer_key in LAYER_KEYS:
                if line.startswith(layer_key):
                    self.layer += 1
                    self.record_layer(self.layer)
                    self.reactor.pause(self.reactor.monotonic() + 0.001)
                    if not self.layer_key:
                        self.layer_key = layer_key
                    break

    def first_floor_pause(self, line, toolhead):
        if self.print_first_layer and self.count_G1 >= 20:
            for layer_key in LAYER_KEYS:
                if line.startswith(layer_key):
                    logging.info("print_first_layer layer_key:%s" % layer_key)
                    X, Y, Z, E = toolhead.get_position()
                    self.gcode.run_script("FIRST_FLOOR_PAUSE")
                    self.first_layer_stop = True

    def check_end_print(self, line, power_loss_switch, delay_photography_switch, frame):
        if not power_loss_switch:
            return
        if line.startswith("END_PRINT") and delay_photography_switch and os.path.exists("/tmp/camera_main"):
            self.printer.send_event("v_sd:update_filament_used")
            self.gcode.run_script("END_PRINT_POINT_WITHOUT_LIFTING")
            self.gcode.run_script("M400")
            capture(end_print=True, frame=frame)
            self.reactor.pause(self.reactor.monotonic() + 1.2)

    # Background work timer
    def work_handler(self, eventtime):
        logging.info("work_handler start print, filename:%s" % self.current_file.name)
        # self.print_stats.note_start()
        self.count_line = 0
        self.count_G1 = 0 
        self.eepromWriteCount = 1
        gcode_move = self.printer.lookup_object('gcode_move', None)
        delay_photography_switch, location, frame, interval, power_loss_switch = self.get_delay_photography_info()
        bl24c16f = self.printer.lookup_object('bl24c16f') if "bl24c16f" in self.printer.objects and power_loss_switch else None
        eepromState = True
        try:
            # 断电续打恢复
            eepromState = self.restore_print(gcode_move, power_loss_switch, bl24c16f, eepromState)
        except Exception as err:
            self.print_stats.power_loss = 0
            logging.exception("work_handler RESTORE_GCODE_STATE error: %s" % err)
        # 记录打印的文件名
        if power_loss_switch and bl24c16f and self.current_file:
            gcode_move.recordPrintFileName(self.print_file_name_path, self.current_file.name)
        logging.info("Starting SD card print (position %d)", self.file_position)
        self.reactor.unregister_timer(self.work_timer)
        try:
            self.current_file.seek(self.file_position)
        except:
            logging.exception("virtual_sdcard seek")
            self.work_timer = None
            return self.reactor.NEVER
        self.print_stats.note_start()
        gcode_mutex = self.gcode.get_mutex()
        partial_input = ""
        lines = []
        error_message = None
        # 判断是否运行热床调平
        if self.run_bed_mesh_calibate:
            self.run_bed_mesh_calibate = False
            cmd = "BED_MESH_CALIBRATE_START_PRINT GCODE_FILE='%s'" % self.current_file.name
            try:
                # 当前目标温度大于热床调平时的默认温度时,用当前目标温度调平
                custom_macro = self.printer.lookup_object('custom_macro')
                heater_bed = self.printer.lookup_object('heater_bed').heater
                target_temp = heater_bed.target_temp
                default_bed_temp = custom_macro.default_bed_temp
                if target_temp > default_bed_temp:
                    cmd += " BED_TEMP=%s" % target_temp
            except Exception as err:
                logging.exception("run_bed_mesh_calibate error: %s" % err)
            self.gcode.run_script(cmd)
        # self.gcode.run_script("G90")
        toolhead = self.printer.lookup_object('toolhead')
        pause_resume = self.printer.lookup_object('pause_resume')
        toolhead.extrude_below_min_temp_err_is_report = False
        start_time = interval_start_time = self.reactor.monotonic()
        self.last_layer = self.layer
        while not self.must_pause_work:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                except UnicodeDecodeError as err:
                    logging.exception(err)
                    err_msg = '{"code": "key571", "msg": "File UnicodeDecodeError"}'
                    self.gcode._respond_error(err_msg)
                    self.gcode.run_script("CANCEL_PRINT")
                    break
                except:
                    logging.exception("virtual_sdcard read")
                    break
                if not data:
                    # End of file
                    self.current_file.close()
                    self.current_file = None
                    logging.info("Finished SD card print")
                    self.gcode.respond_raw("Done printing file")
                    if os.path.exists(self.print_file_name_path):
                        os.remove(self.print_file_name_path)
                    if os.path.exists(self.gcode.exclude_object_info):
                        os.remove(self.gcode.exclude_object_info)
                    if power_loss_switch and bl24c16f:
                        self.gcode.run_script("EEPROM_WRITE_BYTE ADDR=1 VAL=255")
                    self.first_layer_stop = False
                    self.print_first_layer = False
                    self.count_M204 = 0
                    self.layer = 0
                    self.layer_count = 0
                    self.fan_state = {}
                    self.update_print_history_info(only_update_status=True, state="completed")
                    if self.print_id and not self.end_print_state and os.path.exists("/tmp/camera_main"):
                        reportInformation("key608", data={"print_id": self.print_id})
                    self.reactor.pause(self.reactor.monotonic() + 0.3)
                    reportInformation("key701", data=self.cur_print_data)
                    self.cur_print_data = {}
                    self.print_id = ""
                    break
                lines = data.split('\n')
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            # Pause if any other request is pending in the gcode class
            if gcode_mutex.test():
                self.reactor.pause(self.reactor.monotonic() + 0.100)
                continue
            # Dispatch command
            self.cmd_from_sd = True
            line = lines.pop()
            # next_file_position = self.file_position + len(line) + 1
            next_file_position = self.file_position + len(line.encode('utf-8')) + 1
            self.next_file_position = next_file_position
            end_time = interval_end_time = self.reactor.monotonic()
            # 更新当前打印信息,已打印时间、剩余时间等, 断电续打开关开启的情况下才进行下面的判断
            if power_loss_switch and self.count_line % 4999 == 0:
                self.update_print_history_info()
            try:
                # 记录断电续打信息, 断电续打开关开启的情况下才进行下面的判断
                start_time, end_time, interval_start_time, interval_end_time = self.record_power_loss_info(power_loss_switch, bl24c16f,eepromState, 
                                                                                                           gcode_move, start_time, end_time, interval_start_time, interval_end_time)
                # 判断line 是否为M106或者END_PRINT来做一些操作
                line = self.judge_line_starts_with(line, power_loss_switch, bl24c16f)
                # 判断line 记录层信息
                self.record_layer_info(line, power_loss_switch)
                # 根据情况进行首层暂停打印
                self.first_floor_pause(line, toolhead)
                # 将断电续打的慢速打印恢复到正常速度
                if self.slow_print == True and self.layer > 0 and self.slow_count < self.layer:
                    self.resume_print_speed()
                # 在读到END_PRINT的时候 判断是否需要拍照
                self.check_end_print(line, power_loss_switch, delay_photography_switch, frame)
                if self.is_move_out_of_range_in_printing and pause_resume.pause_start == False:
                    self.is_move_out_of_range_in_printing = False
                    self.gcode.run_script_from_command("PAUSE")
                    self.is_move_out_of_range_in_printing = False
                else:
                    self.gcode.run_script(line)
                if power_loss_switch:
                    self.count_line += 1
                if self.count_G1 < 20 and line.startswith("G1"):
                    self.count_G1 += 1
            except self.gcode.error as e:
                error_message = str(e)
                try:
                    self.gcode.run_script(self.on_error_gcode.render())
                except:
                    logging.exception("virtual_sdcard on_error")
                self.layer = 0
                self.layer_count = 0
                self.resume_print_speed()
                break
            except:
                logging.exception("virtual_sdcard dispatch")
                self.layer = 0
                self.layer_count = 0
                self.resume_print_speed()
                break
            self.cmd_from_sd = False
            self.file_position = self.next_file_position
            # Do we need to skip around?
            if self.next_file_position != next_file_position:
                try:
                    self.current_file.seek(self.file_position)
                except:
                    logging.exception("virtual_sdcard seek")
                    self.work_timer = None
                    return self.reactor.NEVER
                lines = []
                partial_input = ""
        logging.info("Exiting SD card print (position %d)", self.file_position)
        self.count_line = 0
        self.count_G1 = 0
        self.do_resume_status = False
        self.eepromWriteCount = 1
        self.work_timer = None
        self.cmd_from_sd = False
        toolhead.extrude_below_min_temp_err_is_report = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
        elif self.current_file is not None:
            if self.is_cancel:
                self.print_stats.note_cancel()
            else:
                self.print_stats.note_pause()
        else:
            self.print_stats.note_complete()
        return self.reactor.NEVER

def load_config(config):
    return VirtualSD(config)
