#!/bin/sh

KEYBOX_SCRIPT=/usr/bin/keybox

if [ $# -eq 1  -o $# -eq 2 ]; then

    PARAM=$(echo $1 | tr 'A-Z' 'a-z')

    PCBA_TEST=$($KEYBOX_SCRIPT -r pcba_test | grep 'pcba_test = ' | awk -F'pcba_test = ' '{print $NF}')
    STRESS_TEST=$($KEYBOX_SCRIPT -r stress_test | grep 'stress_test = ' | awk -F'stress_test = ' '{print $NF}')

    if [ $# -eq 1 ]; then
        if [ "$PARAM" = "pcba_test" -a "x$PCBA_TEST" = "x1" ]; then
            $KEYBOX_SCRIPT -w pcba_test 0 2>&1 >/dev/null

        else
            exit 1
        fi
    elif [ $# -eq 2 ]; then
        if [ "$PARAM" = "machine_sn" -a "x$2" != "x" ]; then
            $KEYBOX_SCRIPT -w machine_sn $2 2>&1 >/dev/null

        elif [ "$PARAM" = "stress_test" -a "x$2" != "x" ]; then
            $KEYBOX_SCRIPT -w stress_test $2 2>&1 >/dev/null

        elif [ "$PARAM" = "pcba_test" -a "x$2" = "x1" ]; then
            $KEYBOX_SCRIPT -w pcba_test 1 2>&1 >/dev/null

        elif [ "$PARAM" = "structure_version" -a "x$2" != "x" ]; then
            $KEYBOX_SCRIPT -w struct_ver $2 2>&1 >/dev/null

        fi
    fi

else
    exit 1
fi
