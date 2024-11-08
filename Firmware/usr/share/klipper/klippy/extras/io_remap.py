import mcu, logging

class CommandError(Exception):
    pass

"""
[io_remap]
src_x_pin: PA1    # 输入pin脚索引号(被映射)
src_y_pin: PA8    # 输入pin脚索引号(被映射)
remap_pin: PA15   # 输出pin脚索引号(映射)
src_x_pullup: 1   # 输入pin脚的上下拉配置,1表示上拉(意味着读取到0表示触发),0表示下拉(意味着读取到1表示触发)
src_y_pullup: 1   # 输入pin脚的上下拉配置,1表示上拉(意味着读取到0表示触发),0表示下拉(意味着读取到1表示触发)
remap_def: 1      # 输出pin脚的默认输出电平
filterNum: 1      # 当读取输入pin脚有效电平持续时间大于等于filterNum * periodTicks, 置输出pin脚为有效电平状态。如果输入的参数为0, 将采用默认值5
periodTicks: 0    # 轮询输入pin脚周期, 单位ticks。如果输入的参数为0, 采用50uS对应的tick默认值
"""

class IORemap:
    error = CommandError
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.src_x_pin = self.config.getsection('io_remap').get('src_x_pin')
        self.src_y_pin = self.config.getsection('io_remap').get('src_y_pin')
        self.remap_pin = self.config.getsection('io_remap').get('remap_pin')
        self.src_x_pullup = self.config.getsection('io_remap').getint('src_x_pullup')
        self.src_y_pullup = self.config.getsection('io_remap').getint('src_y_pullup')
        self.remap_def = self.config.getsection('io_remap').getint('remap_def')
        self.filterNum = self.config.getsection('io_remap').getint('filterNum')
        self.periodTicks = self.config.getsection('io_remap').getint('periodTicks')
        self.mcu = mcu.get_printer_mcu(self.printer, "nozzle_mcu")
        self.oidx = self.mcu.create_oid()
        self.oidy = self.mcu.create_oid()
        self.mcu.register_config_callback(self._build_config)
        self.gcode = config.get_printer().lookup_object('gcode')
        self.gcode.register_command("SET_IOREMAP", self.cmd_SET_IOREMAP)

    def _build_config(self):       
        self.mcu.add_config_cmd("config_ioRemap oid=%d src_pin=%s src_pullup=%d remap_pin=%s remap_def=%d"
                                 % (self.oidx,self.src_x_pin,self.src_x_pullup,self.remap_pin,self.remap_def))
        self.mcu.add_config_cmd("config_ioRemap oid=%d src_pin=%s src_pullup=%d remap_pin=%s remap_def=%d"
                                 % (self.oidy,self.src_y_pin,self.src_y_pullup,self.remap_pin,self.remap_def))
        self.gcode.respond_info("config_ioRemap oid=%s src_pin=%s src_pullup=%s remap_pin=%s remap_def=%s"%
                                (self.oidx,self.src_x_pin,self.src_x_pullup,self.remap_pin,self.remap_def))
        self.gcode.respond_info("config_ioRemap oid=%s src_pin=%s src_pullup=%s remap_pin=%s remap_def=%s"%
                                (self.oidy,self.src_y_pin,self.src_y_pullup,self.remap_pin,self.remap_def)) 
    def cmd_SET_IOREMAP(self, gcmd):
        operation = gcmd.get_int('S', 0)
        axes = gcmd.get_int('AXES', 0)
        oid = self.oidx if axes==0 else self.oidy
        operation_ioRemap = self.mcu.lookup_query_command("operation_ioRemap oid=%c operation=%c filterNum=%c periodTicks=%u",
                                                          "query_ioRemap oid=%c sta=%c", oid=oid)
        operation_ioRemap.send([oid, operation, self.filterNum, self.periodTicks])
        self.gcode.respond_info("operation_ioRemap oid=%s operation=%s filterNum=%s periodTicks=%s"%(oid, operation, self.filterNum, self.periodTicks))

def load_config(config):
    return IORemap(config)

