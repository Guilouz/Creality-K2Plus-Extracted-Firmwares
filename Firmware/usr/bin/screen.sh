#!/bin/sh

DISP_DBG_PATH=/sys/kernel/debug/dispdbg

if [ $# -eq 1 ]; then

    cd $DISP_DBG_PATH

    if [ "x$1" = "xon" ]; then

        echo lcd0 > name
        echo enable > command
        echo 1 > start

    elif [ "x$1" = "xoff" ]; then

        echo lcd0 > name
        echo disable > command
        echo 1 > start

    else
        echo "unknown param"
        exit 1
    fi

    exit 0
else

    echo "unsupport operation!"

    exit 1

fi
