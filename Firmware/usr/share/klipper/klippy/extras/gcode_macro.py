# Add ability to define custom g-code macros
#
# Copyright (C) 2018-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import traceback, logging, ast, copy
import jinja2


######################################################################
# Template handling
######################################################################

# Wrapper for access to printer object get_status() methods
class GetStatusWrapper:
    def __init__(self, printer, eventtime=None):
        self.printer = printer
        self.eventtime = eventtime
        self.cache = {}
    def __getitem__(self, val):
        sval = str(val).strip()
        if sval in self.cache:
            return self.cache[sval]
        po = self.printer.lookup_object(sval, None)
        if po is None or not hasattr(po, 'get_status'):
            raise KeyError(val)
        if self.eventtime is None:
            self.eventtime = self.printer.get_reactor().monotonic()
        self.cache[sval] = res = copy.deepcopy(po.get_status(self.eventtime))
        return res
    def __contains__(self, val):
        try:
            self.__getitem__(val)
        except KeyError as e:
            return False
        return True
    def __iter__(self):
        for name, obj in self.printer.lookup_objects():
            if self.__contains__(name):
                yield name

# Wrapper around a Jinja2 template
class TemplateWrapper:
    def __init__(self, printer, env, name, script):
        self.printer = printer
        self.name = name
        self.gcode = self.printer.lookup_object('gcode')
        gcode_macro = self.printer.lookup_object('gcode_macro')
        self.create_template_context = gcode_macro.create_template_context
        try:
            self.template = env.from_string(script)
        except Exception as e:
            # msg = "Error loading template '%s': %s" % (
            #      name, traceback.format_exception_only(type(e), e)[-1])
            msg = """{"code":"key164", "msg": "Error loading template '%s': %s", "values": ["%s", "%s"]}""" % (
                name, traceback.format_exception_only(type(e), e)[-1], name, traceback.format_exception_only(type(e), e)[-1]
            )
            logging.exception(msg)
            raise printer.config_error(msg)
    def render(self, context=None):
        if context is None:
            context = self.create_template_context()
        try:
            return str(self.template.render(context))
        except Exception as e:
            # msg = "Error evaluating '%s': %s" % (
            #     self.name, traceback.format_exception_only(type(e), e)[-1])
            msg = """{"code":"key165", "msg": "Error evaluating '%s': %s", "values": ["%s", "%s"]}""" % (
                self.name, traceback.format_exception_only(type(e), e)[-1],
                self.name, traceback.format_exception_only(type(e), e)[-1]
            )
            logging.exception(msg)
            raise self.gcode.error(msg)
    def run_gcode_from_command(self, context=None):
        self.gcode.run_script_from_command(self.render(context))

# Main gcode macro template tracking
class PrinterGCodeMacro:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.env = jinja2.Environment('{%', '%}', '{', '}')
    def load_template(self, config, option, default=None):
        name = "%s:%s" % (config.get_name(), option)
        if default is None:
            script = config.get(option)
        else:
            script = config.get(option, default)
        return TemplateWrapper(self.printer, self.env, name, script)
    def _action_emergency_stop(self, msg="action_emergency_stop"):
        self.printer.invoke_shutdown("""{"code":"key170", "msg": "Shutdown due to %s", "values": ["%s"]}""" % (msg, msg))
        return ""
    def _action_respond_info(self, msg):
        self.printer.lookup_object('gcode').respond_info(msg)
        return ""
    def _action_raise_error(self, msg):
        raise self.printer.command_error(msg)
    def _action_call_remote_method(self, method, **kwargs):
        webhooks = self.printer.lookup_object('webhooks')
        try:
            webhooks.call_remote_method(method, **kwargs)
        except self.printer.command_error:
            logging.exception("Remote Call Error")
        return ""
    def create_template_context(self, eventtime=None):
        return {
            'printer': GetStatusWrapper(self.printer, eventtime),
            'action_emergency_stop': self._action_emergency_stop,
            'action_respond_info': self._action_respond_info,
            'action_raise_error': self._action_raise_error,
            'action_call_remote_method': self._action_call_remote_method,
        }

def load_config(config):
    return PrinterGCodeMacro(config)


######################################################################
# GCode macro
######################################################################

class GCodeMacro:
    def __init__(self, config):
        if len(config.get_name().split()) > 2:
            raise config.error(
                    # "Name of section '%s' contains illegal whitespace"
                    # % (config.get_name())
                    """{"code":"key166", "msg": "Name of section '%s' contains illegal whitespace", "values": ["%s"]}""" % (
                        config.get_name(), config.get_name(),
                    )
            )
        name = config.get_name().split()[1]
        self.alias = name.upper()
        self.printer = printer = config.get_printer()
        gcode_macro = printer.load_object(config, 'gcode_macro')
        self.template = gcode_macro.load_template(config, 'gcode')
        self.gcode = printer.lookup_object('gcode')
        self.rename_existing = config.get("rename_existing", None)
        self.cmd_desc = config.get("description", "G-Code macro")
        if self.rename_existing is not None:
            if (self.gcode.is_traditional_gcode(self.alias)
                != self.gcode.is_traditional_gcode(self.rename_existing)):
                raise config.error(
                    # "G-Code macro rename of different types ('%s' vs '%s')"
                    """{"code":"key167", "msg": "G-Code macro rename of different types ('%s' vs '%s')", "values": ["%s", "%s"]}"""
                    % (self.alias, self.rename_existing, self.alias, self.rename_existing))
            printer.register_event_handler("klippy:connect",
                                           self.handle_connect)
        else:
            self.gcode.register_command(self.alias, self.cmd,
                                        desc=self.cmd_desc)
        self.gcode.register_mux_command("SET_GCODE_VARIABLE", "MACRO",
                                        name, self.cmd_SET_GCODE_VARIABLE,
                                        desc=self.cmd_SET_GCODE_VARIABLE_help)
        self.in_script = False
        self.variables = {}
        prefix = 'variable_'
        for option in config.get_prefix_options(prefix):
            try:
                self.variables[option[len(prefix):]] = ast.literal_eval(
                    config.get(option))
            except ValueError as e:
                raise config.error(
                    "Option '%s' in section '%s' is not a valid literal" % (
                        option, config.get_name()))
    def handle_connect(self):
        prev_cmd = self.gcode.register_command(self.alias, None)
        if prev_cmd is None:
            raise self.printer.config_error(
                """{"code":"key169", "msg": "Existing command '%s' not found in gcode_macro rename", "values": ["%s"]}""" % (
                    self.alias, self.alias
                )
            )
        pdesc = "Renamed builtin of '%s'" % (self.alias,)
        self.gcode.register_command(self.rename_existing, prev_cmd, desc=pdesc)
        self.gcode.register_command(self.alias, self.cmd, desc=self.cmd_desc)
    def get_status(self, eventtime):
        return self.variables
    cmd_SET_GCODE_VARIABLE_help = "Set the value of a G-Code macro variable"
    def cmd_SET_GCODE_VARIABLE(self, gcmd):
        variable = gcmd.get('VARIABLE')
        value = gcmd.get('VALUE')
        if variable not in self.variables:
            raise gcmd.error("Unknown gcode_macro variable '%s'" % (variable,))
        try:
            literal = ast.literal_eval(value)
        except ValueError as e:
            raise gcmd.error("Unable to parse '%s' as a literal" % (value,))
        v = dict(self.variables)
        v[variable] = literal
        self.variables = v
        try:
            import os, json
            if "z_safe_pause" in variable:
                logging.info("SET_GCODE_VARIABLE variable:%s literal:%s" % (variable, literal))
                v_sd = self.printer.lookup_object('virtual_sdcard', None)
                if os.path.exists(v_sd.print_file_name_path):
                    result = {}
                    with open(v_sd.print_file_name_path, "r") as f:
                        result = (json.loads(f.read()))
                        result["variable_z_safe_pause"] = literal
                    with open(v_sd.print_file_name_path, "w") as f:
                        f.write(json.dumps(result))
                        f.flush()
        except Exception as err:
            logging.error("SET_GCODE_VARIABLE save z_safe_pause err:%s" % err)
    def cmd(self, gcmd):
        if self.in_script:
            # raise gcmd.error("Macro %s called recursively" % (self.alias,))
            raise gcmd.error("""{"code":"key172", "msg": "Macro %s called recursively", "values": ["%s"]}""" % (self.alias, self.alias))
        kwparams = dict(self.variables)
        kwparams.update(self.template.create_template_context())
        kwparams['params'] = gcmd.get_command_parameters()
        kwparams['rawparams'] = gcmd.get_raw_command_parameters()
        self.in_script = True
        if self.alias == "PAUSE":
            self.printer.lookup_object('pause_resume').pause_start = True
        try:
            self.template.run_gcode_from_command(kwparams)
        finally:
            self.in_script = False
            if self.alias == "PAUSE":
                self.printer.lookup_object('pause_resume').pause_start = False
            elif self.alias == "MOTOR_CANCEL_PRINT":
                self.printer.lookup_object('pause_resume').motor_cancel_print_start = False
def load_config_prefix(config):
    return GCodeMacro(config)
