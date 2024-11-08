#!/bin/sh

# nozzle cam
# SOC: PF2 --> PIN: 162
USB_P_EN3=162

if [ $# -eq 1 ]; then

    [ -L /sys/class/gpio/gpio$USB_P_EN3 ] || exit 1

    if [ "x$1" = "xon" ]; then
        # nozzle camera, turn on
        echo 0 > /sys/class/gpio/gpio$USB_P_EN3/value
    elif [ "x$1" = "xoff" ]; then
        # nozzle camera, turn off
        echo 1 > /sys/class/gpio/gpio$USB_P_EN3/value
    fi
else
    exit 1
fi
