# TMC2262 configuration
#
# Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging
from . import bus, tmc, tmc2130

Registers = {
    "GCONF":                    0x00,
    "GSTAT":                    0x01,
    "DIAG_CONF":                0x02,
    "DIAG_DAC_CONF":            0x03,
    "IOIN":                     0x04,
    "DRV_CONF":                 0x0A,
    "PLL":                      0x0B,
    "IHOLD_IRUN":               0x10,
    "TPOWERDOWN":               0x11,
    "TSTEP":                    0x12,
    "TPWMTHRS":                 0x13,
    "TCOOLTHRS":                0x14,
    "THIGH":                    0x15,
    "TSGP_LOW_VEL_THRS":        0x16,
    "T_RCOIL_MEAS":             0x17,
    "TUDCSTEP":                 0x18,
    "UDC_CONF":                 0x19,
    "STEPS_LOST":               0x1A,
    "SW_MODE":                  0x34,
    "SG_SEQ_STOP_STAT":         0x35,
    "ENCMODE":                  0x38,
    "X_ENC":                    0x39,
    "ENC_CONST":                0x3A,
    "ENC_STATUS":               0x3B,
    "ENC_LATCH":                0x3C,
    "ENC_DEVIATION":            0x3D,
    "CURRENT_PI_REG":           0x40,
    "ANGLE_PI_REG":             0x41,
    "CUR_ANGLE_LIMIT":          0x42,
    "ANGLE_LOWER_LIMIT":        0x43,
    "CUR_ANGLE_MEAS":           0x44,
    "PI_RESULTS":               0x45,
    "COIL_INDUCT":              0x46,
    "R_COIL":                   0x47,
    "R_COIL_USER":              0x48,
    "SGP_CONF":                 0x49,
    "SGP_IND_2_3":              0x4A,
    "SGP_IND_0_1":              0x4B,
    "INDUCTANCE_VOLTAGE":       0x4C,
    "SGP_BEMF":                 0x4D,
    "COOLSTEPPLUS_CONF":        0x4E,
    "COOLSTEPPLUS_PI_REG":      0x4F,
    "COOLSTEPPLUS_PI_DOWN":     0x50,
    "COOLSTEPPLUS_RESERVE_CONF":0x51,
    "COOLSTEPPLUS_LOAD_RESERVE":0x52,
    "TSTEP_VELOCITY":           0x53,
    "ADC_VSUPPLY_TEMP":         0x58,
    "ADC_I":                    0x59,
    "OTW_OV_VTH":               0x5A,
    "MSLUT0":                   0x60,
    "MSLUT1":                   0x61,
    "MSLUT2":                   0x62,
    "MSLUT3":                   0x63,
    "MSLUT4":                   0x64,
    "MSLUT5":                   0x65,
    "MSLUT6":                   0x66,
    "MSLUT7":                   0x67,
    "MSLUTSEL":                 0x68,
    "MSLUTSTART":               0x69,
    "MSCNT":                    0x6A,
    "MSCURACT":                 0x6B,
    "CHOPCONF":                 0x6C,
    "COOLCONF":                 0x6D,
    "DRV_STATUS":               0x6F,
    "PWMCONF":                  0x70,
}

# ReadRegisters = [
#     "GCONF", "CHOPCONF", "GSTAT", "DRV_STATUS", "IOIN", "PLL",
#     "MSCNT", "MSCURACT", "TSTEP"
# ]

ReadRegisters = [
    "GCONF", "DIAG_CONF", "DRV_CONF", "IHOLD_IRUN", "TPOWERDOWN", "TPWMTHRS",
    "UDC_CONF", "CHOPCONF", "COOLCONF", "PWMCONF", "GSTAT", "DRV_STATUS", "PLL", "IOIN",
    "SG_SEQ_STOP_STAT", "MSCNT"
]

Fields = {}
Fields["COOLCONF"] = {
    "semin":                    0x0F << 0,
    "seup":                     0x03 << 5,
    "semax":                    0x0F << 8,
    "sedn":                     0x07 << 13,
    "seimin":                   0x01 << 15,
    "sgt":                      0x7F << 16,
    "thigh_sg_off":             0x01 << 23,
    "sfilt":                    0x01 << 24
}
Fields["CHOPCONF"] = {
    "toff":                     0x0F << 0,
    "hstrt":                    0x07 << 4,
    "hend":                     0x0F << 7,
    "fd3":                      0x01 << 11,
    "disfdcc":                  0x01 << 12,
    "chm":                      0x01 << 14,
    "tbl":                      0x03 << 15,
    "tpfd":                     0x0F << 20, # midrange resonances
    "mres":                     0x0F << 24,
    "intpol":                   0x01 << 28,
    "dedge":                    0x01 << 29
}
Fields["DRV_CONF"] = {
    "current_range":            0x03 << 0,
    "current_range_scale":      0x03 << 2,
    "clope_control":            0x03 << 4,
}
Fields["DRV_STATUS"] = {
    "sg_result":                0x3FF << 0,
    "seq_stopped":              0x01 << 10,
    "ov":                       0x01 << 11,
    "s2vsa":                    0x01 << 12,
    "s2vsb":                    0x01 << 13,
    "stealth":                  0x01 << 14,
    "cs_actual":                0xFF << 16,
    "stallguard":               0x01 << 24,
    "ot":                       0x01 << 25,
    "otpw":                     0x01 << 26,
    "s2ga":                     0x01 << 27,
    "s2gb":                     0x01 << 28,
    "ola":                      0x01 << 29,
    "olb":                      0x01 << 30,
    "stst":                     0x01 << 31
}

Fields["GCONF"] = {
    "fast_standstill":          0x01 << 0,
    # "en_stealthc":              0x01 << 1, # datasheet的叫法
    "en_pwm_mode":              0x01 << 1, # 实际使用叫法(tmc.py内使用)
    "multistep_filt":           0x01 << 2,
    "shaft":                    0x01 << 3,
    "small_hysteresis":         0x01 << 4,
    "stop_enable":              0x01 << 5,
    "direct_mode":              0x01 << 6,
    "length_steppulse":         0x0F << 8,
    "OV_nN":                    0x01 << 9,
    "step_dir":                 0x01 << 31
}
Fields["GSTAT"] = {
    "reset":                    0x01 << 0,
    "drv_err":                  0x01 << 1,
    "uv_cp":                    0x01 << 2,
    "register_reset":           0x01 << 3,
    "vm_uvlo":                  0x01 << 4,
    "vccio_uv":                 0x01 << 5
}
Fields["IHOLD_IRUN"] = {
    "ihold":                    0xFF << 0,
    "irun":                     0xFF << 8,
    "iholddelay":               0x3F << 16
}
Fields["IOIN"] = {
    "refl":                     0x01 << 0,
    "refr":                     0x01 << 1,
    "encb":                     0x01 << 2,
    "enca":                     0x01 << 3,
    "drv_enn":                  0x01 << 4,
    "encn":                     0x01 << 5,
    "ext_res_det":              0x01 << 13,
    "ext_clk":                  0x01 << 14,
    "silicon_rv":               0x03 << 16
}
Fields["MSLUT0"] = {
    "mslut0":                   0xffffffff
}
Fields["MSLUT1"] = {
    "mslut1":                   0xffffffff
}
Fields["MSLUT2"] = {
    "mslut2":                   0xffffffff
}
Fields["MSLUT3"] = {
    "mslut3":                   0xffffffff
}
Fields["MSLUT4"] = {
    "mslut4":                   0xffffffff
}
Fields["MSLUT5"] = {
    "mslut5":                   0xffffffff
}
Fields["MSLUT6"] = {
    "mslut6":                   0xffffffff
}
Fields["MSLUT7"] = {
    "mslut7":                   0xffffffff
}
Fields["MSLUTSEL"] = {
    "w0":                       0x03 << 0,
    "w1":                       0x03 << 2,
    "w2":                       0x03 << 4,
    "w3":                       0x03 << 6,
    "x1":                       0xFF << 8,
    "x2":                       0xFF << 16,
    "x3":                       0xFF << 24
}
Fields["MSLUTSTART"] = {
    "start_sin":                0xFF << 0,
    "start_sin90":              0xFF << 16,
    "offset_sin90":             0xFF << 24,
}
Fields["MSCNT"] = {
    "mscnt":                    0x3ff << 0
}
Fields["MSCURACT"] = {
    "cur_a":                    0x1ff << 0,
    "cur_b":                    0x1ff << 16
}
Fields["PWMCONF"] = {
    "pwm_freq":                 0x0F << 0,
    "freewheel":                0x03 << 4,
    "ol_thrsh":                 0x03 << 6,
    "sd_on_meas_lo":            0x01 << 12,
    "sd_on_meas_hi":            0x01 << 16,
}
Fields["TPOWERDOWN"] = {
    "tpowerdown":               0xff << 0
}
Fields["TPWMTHRS"] = {
    "tpwmthrs":                 0xfffff << 0
}
Fields["TCOOLTHRS"] = {
    "tcoolthrs":                0xfffff << 0
}
Fields["TSTEP"] = {
    "tstep":                    0xfffff << 0
}
Fields["THIGH"] = {
    "thigh":                    0xfffff << 0
}

Fields["PLL"] = {
    "commit":                   0x1 << 0,
    "ext_not_int":              0x1 << 1,
    "clk_sys_sel":              0x1 << 2,
    "adc_clk_ena":              0x3 << 3,
    "clock_divider":            0x1f << 5,
    "clk_fsm_ena":              0x1 << 10,
    "clk_1mo_tmo":              0x1 << 12,
    "clk_loss":                 0x1 << 13,
    "clk_is_stuck":             0x1 << 14,
    "pll_lock_loss":            0x1 << 15
}

SignedFields = ["cur_a", "cur_b", "sgt", "pwm_scale_auto"]

FieldFormatters = {
    "shaft":            (lambda v: "1(Reverse)" if v else ""),
    "reset":            (lambda v: "1(Reset)" if v else ""),
    "drv_err":          (lambda v: "1(ErrorShutdown!)" if v else ""),
    "uv_cp":            (lambda v: "1(Undervoltage!)" if v else ""),
    "mres":             (lambda v: "%d(%dusteps)" % (v, 0x100 >> v)),
    "otpw":             (lambda v: "1(OvertempWarning!)" if v else ""),
    "ot":               (lambda v: "1(OvertempError!)" if v else ""),
    "s2ga":             (lambda v: "1(ShortToGND_A!)" if v else ""),
    "s2gb":             (lambda v: "1(ShortToGND_B!)" if v else ""),
    "ola":              (lambda v: "1(OpenLoad_A!)" if v else ""),
    "olb":              (lambda v: "1(OpenLoad_B!)" if v else ""),
    "cs_actual":        (lambda v: ("%d" % v) if v else "0(Reset?)"),
    "s2vsa":            (lambda v: "1(ShortToSupply_A!)" if v else ""),
    "s2vsb":            (lambda v: "1(ShortToSupply_B!)" if v else ""),
}

MAX_CURRENT = 4.24

class TMC2262CurrentHelper:
    def __init__(self, config, mcu_tmc):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.mcu_tmc = mcu_tmc
        self.fields = mcu_tmc.get_fields()
        self.current_range = config.getint('current_range', 0, minval=0, maxval=3)
        self.current_range_scale = config.getint('current_range_scale', 0, minval=0, maxval=3)
        if self.current_range >= 1:
            self.current_range_scale = 3
        Rref = config.getint('rref', 12000, minval=12000, maxval=60000)
        Kifs_values = {
            0: 18000,
            1: 36000,
            2: 54000,
            3: 72000
        }
        CRS_values = {
            3: 1.,
            2: 0.75,
            1: 0.5,
            0: 0.25
        }
        self.IFS_current_RMS = (float(CRS_values[self.current_range_scale] * 
                                    Kifs_values[self.current_range]) /
                                    Rref) / math.sqrt(2.)
        run_current = config.getfloat('run_current',
                                      above=0., maxval=self.IFS_current_RMS)
        hold_current = config.getfloat('hold_current', self.IFS_current_RMS,
                                       above=0., maxval=self.IFS_current_RMS)
        self.req_hold_current = hold_current
        irun, ihold = self._calc_current(run_current, hold_current)
        self.fields.set_field("ihold", ihold)
        self.fields.set_field("irun", irun)
        self.fields.set_field("current_range", self.current_range)
        self.fields.set_field("current_range_scale", self.current_range_scale)
    def _calc_current_bits(self, current):
        cs = int(current * 250 / MAX_CURRENT * 4 / (self.current_range + 1) * 4 / (self.current_range_scale + 1) + .5)
        return max(0, min(0xFF, cs))
    def _calc_current(self, run_current, hold_current):
        irun = self._calc_current_bits(run_current)
        ihold = self._calc_current_bits(min(hold_current, run_current))
        return irun, ihold
    def _calc_current_from_field(self, field_name):
        current_range = self.fields.get_field("current_range")
        current_range_scale = self.fields.get_field("current_range_scale")
        bits = self.fields.get_field(field_name)
        return MAX_CURRENT * (current_range + 1) / 4 * (current_range_scale + 1) / 4 * bits / 250
    def get_current(self):
        run_current = self._calc_current_from_field("irun")
        hold_current = self._calc_current_from_field("ihold")
        return (run_current, hold_current, self.req_hold_current,
            self.IFS_current_RMS)
    def set_current(self, run_current, hold_current, print_time):
        irun, ihold = self._calc_current(run_current, hold_current)
        logging.info("hys: irun = %s, ihold = %s" % (irun, ihold))
        self.fields.set_field("ihold", ihold)
        val = self.fields.set_field("irun", irun)
        self.mcu_tmc.set_register("IHOLD_IRUN", val, print_time)

class TMC2262PLLHelper:
    def __init__(self, config):
        ext_clk_set = config.getboolean('driver_EXT_NOT_INT', False)
        logging.info("driver_EXT_NOT_INT: %s" % ext_clk_set)
        self.tmc_frequency = config.getfloat('tmc_frequency',
            16000000., minval=1000000., maxval=32000000.)
        if not ext_clk_set:
            # internal clock -- 16MHz
            self.tmc_frequency = 16000000.

    def _calc_clock_divider(self):
        clock_divider = int(self.tmc_frequency / 1000000 - 1 + 0.5)
        logging.info("hys: clock_divider = 0x%x" % clock_divider)
        return clock_divider

######################################################################
# TMC2262 printer object
######################################################################

class TMC2262:
    def __init__(self, config):
        # Setup mcu communication
        self.fields = tmc.FieldHelper(Fields, SignedFields, FieldFormatters)
        set_config_field = self.fields.set_config_field
        #   GCONF
        set_config_field(config, "multistep_filt", True)
        set_config_field(config, "step_dir", True)
        #   PLL (tmc2262 special register)
        set_config_field(config, "ext_not_int", False)
        PLL_helper = TMC2262PLLHelper(config)
        self.tmc_frequency = PLL_helper.tmc_frequency
        set_config_field(config, "clock_divider", PLL_helper._calc_clock_divider())
        self.mcu_tmc = tmc2130.MCU_TMC_SPI(config, Registers, self.fields)
        # Allow virtual pins to be created
        tmc.TMCVirtualPinHelper(config, self.mcu_tmc)
        # Register commands
        current_helper = TMC2262CurrentHelper(config, self.mcu_tmc)
        cmdhelper = tmc.TMCCommandHelper(config, self.mcu_tmc, current_helper)
        # Set microstep config options
        tmc.TMCMicrostepHelper(config, self.mcu_tmc)
        cmdhelper.setup_register_dump(ReadRegisters)
        self.get_phase_offset = cmdhelper.get_phase_offset
        self.get_status = cmdhelper.get_status
        # Setup basic register values
        tmc.TMCWaveTableHelper(config, self.mcu_tmc)
        tmc.TMCStealthchopHelper(config, self.mcu_tmc, self.tmc_frequency)
        tmc.TMCVcoolthrsHelper(config, self.mcu_tmc, self.tmc_frequency)
        #   CHOPCONF
        set_config_field(config, "toff", 3)
        set_config_field(config, "hstrt", 5)
        set_config_field(config, "hend", 2)
        set_config_field(config, "fd3", 0)
        set_config_field(config, "disfdcc", 0)
        set_config_field(config, "chm", 0)
        set_config_field(config, "tbl", 2)
        set_config_field(config, "tpfd", 4)
        set_config_field(config, "intpol", 1)
        set_config_field(config, "dedge", 0)
        #   COOLCONF
        set_config_field(config, "semin", 0)
        set_config_field(config, "seup", 0)
        set_config_field(config, "semax", 0)
        set_config_field(config, "sedn", 0)
        set_config_field(config, "seimin", 0)
        set_config_field(config, "sgt", 0)
        set_config_field(config, "sfilt", 0)
        #   IHOLDIRUN
        set_config_field(config, "iholddelay", 6)
        #   PWMCONF
        set_config_field(config, "pwm_freq", 0)
        set_config_field(config, "freewheel", 0)
        #   TPOWERDOWN
        set_config_field(config, "tpowerdown", 10)
        #   IOIN
        set_config_field(config, "drv_enn", False)

def load_config_prefix(config):
    return TMC2262(config)
