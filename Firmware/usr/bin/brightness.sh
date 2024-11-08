#!/bin/sh

#set -x

# MAX_BRIGHTNESS=/sys/class/backlight/backlight_pwm0/max_brightness
MAX_BRIGHTNESS=255
BKL_PATH=/sys/kernel/debug/dispdbg/
# BRIGHTNESS=/sys/class/backlight/backlight_pwm0/brightness
DARK_BRIGHTNESS=25

if [ $# -eq 1 ]; then

    [ $1 -lt 0 -o $1 -gt 100 ] && echo "invalid brightness" && exit 1

    #echo "set brightness level $1"
    if [ $1 -eq 0 ]; then
        set_pwm=0
    elif [ $1 -eq 100 ]; then
        set_pwm=$MAX_BRIGHTNESS
    elif [ $1 -gt 0 -a $1 -lt 10 ]; then
        set_pwm=$DARK_BRIGHTNESS
    else
        set_pwm=$(( $MAX_BRIGHTNESS * $1 / 100 ))
    fi
    #echo "set_pwm: $set_pwm"

    # echo $set_pwm > $BRIGHTNESS
    echo "setbl" > $BKL_PATH/command
    echo "lcd0" > $BKL_PATH/name
    echo $set_pwm > $BKL_PATH/param
    echo 1 > $BKL_PATH/start

    exit 0

elif [ $# -eq 0 ]; then

    echo getbl > $BKL_PATH/command
    echo lcd0 > $BKL_PATH/name
    echo 1 > $BKL_PATH/start
    cur_pwm=$(cat $BKL_PATH/info)

    #echo "current brightness $cur_pwm"
    if [ $cur_pwm -eq 0 ]; then
        cur_level=0
    elif [ $cur_pwm -eq $MAX_BRIGHTNESS ]; then
        cur_level=100
    elif [ $cur_pwm -le $DARK_BRIGHTNESS ]; then
        cur_level=1
    else
        cur_level=$(( $cur_pwm * 100 / $MAX_BRIGHTNESS ))
    fi

    echo "$cur_level"

    exit 0

else

    echo "unsupport operation!"

    exit 1

fi
