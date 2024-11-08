from . import auto_addr_wrapper
def load_config(config):
    aa = auto_addr_wrapper.AutoAddrWrapper(config)
    return aa
