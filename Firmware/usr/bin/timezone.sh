#!/bin/sh

#set -x

ZONEINFO_DIR=/usr/share/zoneinfo
TIMEZONE_FILE=/tmp/localtime
CUR_ZONENAME=$(uci get system.@system[0].zonename)

#UTC timezone;zonename
TIMEZONE_LIST=$(cat << EOF
UTC-12:00;GMT+12
UTC-11:00;US/Samoa
UTC-10:00;Pacific/Honolulu
UTC-09:30;Pacific/Marquesas
UTC-09:00;US/Alaska
UTC-08:00;US/Pacific
UTC-07:00;US/Mountain
UTC-06:00;US/Central
UTC-05:00;EST
UTC-04:00;America/Halifax
UTC-03:30;Canada/Newfoundland
UTC-03:00;Brazil/East
UTC-02:00;GMT+2
UTC-01:00;Atlantic/Cape_Verde
UTC+00:00;WET
UTC+01:00;Europe/Madrid
UTC+02:00;EET
UTC+03:00;W-SU
UTC+03:30;Iran
UTC+04:00;GMT-4
UTC+04:30;Asia/Kabul
UTC+05:00;Asia/Karachi
UTC+05:30;Asia/Kolkata
UTC+05:45;Asia/Kathmandu
UTC+06:00;Asia/Dhaka
UTC+06:30;Asia/Rangoon
UTC+07:00;Asia/Saigon
UTC+08:00;Asia/Shanghai
UTC+09:00;Asia/Tokyo
UTC+09:30;Australia/North
UTC+10:00;Australia/Victoria
UTC+10:30;Australia/Lord_Howe
UTC+11:00;Pacific/Guadalcanal
UTC+12:00;Pacific/Auckland
UTC+12:45;Pacific/Chatham
UTC+13:00;Pacific/Enderbury
UTC+14:00;Pacific/Kiritimati
EOF
)

get_timezone()
{
    for zonename in $(echo "$TIMEZONE_LIST" | awk 'BEGIN{FS=";"} {print $2;}')
    do
        if [ "$zonename" = "$CUR_ZONENAME" ]; then
            #echo "find: $zonename"
            timezone=$(echo "$TIMEZONE_LIST" | awk -v zonename=$zonename 'BEGIN{FS=";"} {if ($2 == zonename) print $1;}')
            echo "$timezone"
        fi
    done
}

set_timezone()
{
    for timezone in $(echo "$TIMEZONE_LIST" | awk 'BEGIN{FS=";"} {print $1;}')
    do
        if [ "$1" = "$timezone" ]; then
            #echo "find: $timezone"
            zonename=$(echo "$TIMEZONE_LIST" | awk -v timezone=$timezone 'BEGIN{FS=";"} {if ($1 == timezone) print $2;}')
            #echo "set: $zonename"
            uci set system.@system[0].zonename="$zonename"
            uci commit
            reload_config

            ln -sf ..$ZONEINFO_DIR/$zonename $TIMEZONE_FILE
        fi
    done
}

if [ $# -eq 1 ]; then
    set_timezone $1

elif [ $# -eq 0 ]; then
    get_timezone

else
    echo "unsupport operation!"

    exit 1
fi
