#!/bin/sh

# USB HUB reset
# SOC: PF5 --> PIN: 165
USB_HUB_RST=165

# udisk 
# SOC: PG18 --> PIN: 210
USB_P_EN1=210

# CMS, reserved 
# SOC: PG17 --> PIN: 209
USB_P_EN2=209

# nozzle cam
# SOC: PF2 --> PIN: 162
USB_P_EN3=162

# cam, reserved
# SOC: PF1 --> PIN: 161
USB_P_EN4=161

gpio_export()
{
    [ -L /sys/class/gpio/gpio$USB_HUB_RST ] || echo $USB_HUB_RST > /sys/class/gpio/export
    [ -L /sys/class/gpio/gpio$USB_P_EN1 ] || echo $USB_P_EN1 > /sys/class/gpio/export
    [ -L /sys/class/gpio/gpio$USB_P_EN3 ] || echo $USB_P_EN3 > /sys/class/gpio/export

    [ "$model" = "CR-K1 Max" ] && {
        [ -L /sys/class/gpio/gpio$USB_P_EN2 ] || echo $USB_P_EN2 > /sys/class/gpio/export
        [ -L /sys/class/gpio/gpio$USB_P_EN4 ] || echo $USB_P_EN4 > /sys/class/gpio/export
    }
}

gpio_unexport()
{
    [ -L /sys/class/gpio/gpio$USB_HUB_RST ] && echo $USB_HUB_RST > /sys/class/gpio/unexport
    [ -L /sys/class/gpio/gpio$USB_P_EN1 ] && echo $USB_P_EN1 > /sys/class/gpio/unexport
    [ -L /sys/class/gpio/gpio$USB_P_EN3 ] && echo $USB_P_EN3 > /sys/class/gpio/unexport

    [ "$model" = "CR-K1 Max" ] && {
        [ -L /sys/class/gpio/gpio$USB_P_EN2 ] && echo $USB_P_EN2 > /sys/class/gpio/unexport
        [ -L /sys/class/gpio/gpio$USB_P_EN4 ] && echo $USB_P_EN4 > /sys/class/gpio/unexport
    }
}

gpio_init()
{
    echo out > /sys/class/gpio/gpio$USB_HUB_RST/direction
    # 0: reset inactive; 1: reset active
    echo 0 > /sys/class/gpio/gpio$USB_HUB_RST/value

    echo out > /sys/class/gpio/gpio$USB_P_EN1/direction
    # 0: power on; 1: power off
    echo 0 > /sys/class/gpio/gpio$USB_P_EN1/value

    echo out > /sys/class/gpio/gpio$USB_P_EN3/direction
    # 0: power on; 1: power off
    echo 1 > /sys/class/gpio/gpio$USB_P_EN3/value

    [ "$model" = "CR-K1 Max" ] && {
        echo out > /sys/class/gpio/gpio$USB_P_EN2/direction
        # 0: power on; 1: power off
        echo 0 > /sys/class/gpio/gpio$USB_P_EN2/value
    
        echo out > /sys/class/gpio/gpio$USB_P_EN4/direction
        # 0: power on; 1: power off
        echo 0 > /sys/class/gpio/gpio$USB_P_EN4/value
    
    }
}

gpio_uninit()
{
    # 0: power on; 1: power off
    echo 1 > /sys/class/gpio/gpio$USB_P_EN1/value

    # 0: power on; 1: power off
    echo 1 > /sys/class/gpio/gpio$USB_P_EN3/value

    # 0: reset inactive; 1: reset active
    echo 1 > /sys/class/gpio/gpio$USB_HUB_RST/value

    [ "$model" = "CR-K1 Max" ] && {
        # 0: power on; 1: power off
        echo 1 > /sys/class/gpio/gpio$USB_P_EN2/value

        # 0: power on; 1: power off
        echo 1 > /sys/class/gpio/gpio$USB_P_EN4/value
    }
}

help()
{
    echo "Usage: $0 <enable|disable>"
}

if [ $# -eq 1 ]; then
    model=$(get_sn_mac.sh model)

    case $1 in
    "enable")
        gpio_export
        gpio_init
    ;;

    "disable")
        gpio_uninit
        gpio_unexport
    ;;

    *)
        echo "unsupport operation!"
        help
    ;;
    esac

else
    help
    exit 1
fi
