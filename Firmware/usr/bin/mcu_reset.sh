#!/bin/sh

# MCU pwr en
# SOC: PE12 --> PIN: 140
MCU_PWR_EN=140

gpio_export()
{
    [ -L /sys/class/gpio/gpio$MCU_PWR_EN ] || echo $MCU_PWR_EN > /sys/class/gpio/export
}

gpio_unexport()
{
    [ -L /sys/class/gpio/gpio$MCU_PWR_EN ] && echo $MCU_PWR_EN > /sys/class/gpio/unexport
}

gpio_init()
{
    echo out > /sys/class/gpio/gpio$MCU_PWR_EN/direction
    # 0: power on; 1: power off
    echo 0 > /sys/class/gpio/gpio$MCU_PWR_EN/value

}

gpio_uninit()
{
    # 0: power on; 1: power off
    echo 1 > /sys/class/gpio/gpio$MCU_PWR_EN/value
}

pwr_rst()
{
    echo 1 > /sys/class/gpio/gpio$MCU_PWR_EN/value
    sleep 2
    echo 0 > /sys/class/gpio/gpio$MCU_PWR_EN/value
}

help()
{
    echo "Usage: $0 [enable|disable]"
}

if [ $# -eq 1 ]; then

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
elif [ $# -eq 0 ]; then
    pwr_rst
else
    help
    exit 1
fi
