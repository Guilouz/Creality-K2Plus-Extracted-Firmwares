#!/bin/sh

USR_DATA=/mnt/UDISK
IMG_PATH=/usr/res
PROG=/usr/sbin/fbviewer
DISP_ATTR_PATH=/sys/class/disp/disp/attr

img_display()
{
    local xres=$(cat $DISP_ATTR_PATH/xres)
    local yres=$(cat $DISP_ATTR_PATH/yres)
    local img_path=$IMG_PATH/${xres}_${yres}

    while true
    do
        for i in $(ls $img_path/*.jpg | sort -t '/' -k 5 -n) ;
        do
        $PROG $i &
        sleep 1
        killall $PROG
        done
    done
}

if [ $# -eq 0 ]; then

    memtester 64M &
    cd $USR_DATA && stress -i 1 -d 1 --hdd-bytes 128M &
    img_display
else
    echo "Usage: run this script directly!"
fi
