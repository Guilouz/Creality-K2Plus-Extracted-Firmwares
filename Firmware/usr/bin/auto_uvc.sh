#!/bin/sh

#set -x

. /usr/share/libubox/jshn.sh

PROG=/usr/bin/cam_app
PROG_SUB=/usr/bin/cam_sub_app
WEBRTC_LOCAL=/usr/bin/webrtc_local
TMP_VERSION_FILE=/tmp/.cam_version
VERSION_FILE=/mnt/UDISK/creality/userdata/config/cam_version.json
FW_ROOT_DIR=/usr/share/uvc/fw

MAIN_CAM=0
MAIN_PIC_WIDTH=1920
MAIN_PIC_HEIGHT=1080
MAIN_PIC_FPS=15

MODEL=$(/usr/bin/get_sn_mac.sh model)

SUB_CAM=1
SUB_PIC_WIDTH=1600
SUB_PIC_HEIGHT=1200
SUB_PIC_FPS=5

TIME_OUT_CNT=15

echo_console()
{
    printf "$*" > /dev/console
}

fw_info()
{
    case $1 in
        *main*)
            local is_main=1
        ;;
        *sub*)
            local is_main=0
        ;;
    esac

    [ -x /usr/bin/cam_util ] && {
        FwVersion=$(cam_util -i $1 -g | grep -w FwVersion | awk -F ' ' -e '{print $2}')
        if [ "x$FwVersion" != "x" ]; then
            case $FwVersion in
                STD*|C400100*)
                    manufactory=$(echo $FwVersion | cut -d '_' -f 1)
                    cur_version=$(echo $FwVersion | cut -d '_' -f 2)
                    ;;
                *)
                    manufactory=${FwVersion:15:9}
                    cur_version=${FwVersion:25:6}
                    ;;
            esac

            case $manufactory in
                "STsmart_-" | "STJC-000I")
                    if [ $(ls ${FW_ROOT_DIR}/STsmart/*.src | wc -l) -eq 1 ]; then
                        fw_path=$(ls ${FW_ROOT_DIR}/STsmart/*.src)
                        tmp=$(basename $fw_path)
                        tmp=${tmp%.src}
                        fw_version=${tmp##*_}
                    else
                        fw_version=0
                        fw_path=
                        echo_console "we should keep only one firmware file for STsmart!"
                    fi
                    ;;
                *)
                    ;;
            esac

            if [ -f $TMP_VERSION_FILE ]; then
                json_load_file $TMP_VERSION_FILE
            else
                json_init
            fi
            if [ "x$is_main" = "x1" ]; then
                json_add_object "main_cam"
            else
                json_add_object "sub_cam"
            fi
            json_add_string "video_node" $1
            json_add_string "manufactory" $manufactory
            json_add_string "cur_version" $cur_version
            json_add_string "newest_fw_path" $fw_path
            if [ $fw_version -gt $cur_version ]; then
                json_add_boolean "can_update" 1
            else
                json_add_boolean "can_update" 0
            fi
            json_close_object
            json_dump > $TMP_VERSION_FILE
            json_cleanup

            [ "x$is_main" = "x1" ] || return

            json_init
            json_add_object "main_cam"
            json_add_string "manufactory" $manufactory
            json_add_string "cur_version" $cur_version
            json_add_string "power_en_pin" $power_en_pin
            json_close_object
            json_dump > $VERSION_FILE
            json_cleanup
        fi
    }
}

start_uvc()
{
    local count=0
    case $1 in
        main-video*)
            echo_console "start cam_app service for $1 : "
            logger -t uvc "start cam_app service for $1"

            fw_info /dev/v4l/by-id/$1

            # wait for UDISK mounted
            while true
            do
                if grep -wq UDISK /proc/mounts ; then
                    logger -t uvc "UDISK mounted"
                    break
                else
                    sleep 0.4
                    let count+=1
                fi

                # time out 6s
                if [ $count -gt $TIME_OUT_CNT ]; then
                    break
                fi
            done

            start-stop-daemon -S -b -m -p /var/run/$1.pid \
                --exec $PROG -- -i /dev/v4l/by-id/$1 -t $MAIN_CAM \
                -w $MAIN_PIC_WIDTH -h $MAIN_PIC_HEIGHT -f $MAIN_PIC_FPS \
                -c
            [ $? = 0 ] && echo_console "OK\n" || echo_console "FAIL\n"

            sleep 1

            start-stop-daemon -S -b -m -p /var/run/$1_webrtc_local.pid \
                --exec $WEBRTC_LOCAL
        ;;
        sub-video*)
            echo_console "start cam_app service for $1 : "

            fw_info /dev/v4l/by-id/$1

            start-stop-daemon -S -b -m -p /var/run/$1.pid \
                --exec $PROG_SUB -- -i /dev/v4l/by-id/$1 -t $SUB_CAM \
                -w $SUB_PIC_WIDTH -h $SUB_PIC_HEIGHT -f $SUB_PIC_FPS \
                -c
            [ $? = 0 ] && echo_console "OK\n" || echo_console "FAIL\n"
        ;;
    esac
}

stop_uvc()
{
    local count=0
    case $1 in
        main-video*)
            echo_console "stop cam_app service for $1 : "

            start-stop-daemon -K -p /var/run/$1.pid

            if [ $? = 0 ]; then
                echo_console "OK\n"

                # wait for process exit
                while true
                do
                    if [ -d /proc/$(cat /var/run/$1.pid) ]; then
                        sleep 0.2
                        let count+=1
                    else
                        break
                    fi
                    # time out 3s, then send kill signal to process
                    if [ $count -gt $TIME_OUT_CNT ]; then
                        kill -9 $(cat /var/run/$1.pid)
                        sleep 0.5
                    fi
                done

            else
                echo_console "FAIL\n"
            fi

            start-stop-daemon -K -p /var/run/$1_webrtc_local.pid

            [ -f $TMP_VERSION_FILE ] && {
                jq -cMS 'del(.main_cam)' $TMP_VERSION_FILE > $TMP_VERSION_FILE.tmp && \
                mv $TMP_VERSION_FILE.tmp $TMP_VERSION_FILE && sync
                [ $(jq -e 'has("sub_cam")' $TMP_VERSION_FILE) = "true" ] || rm -rf $TMP_VERSION_FILE
            }
        ;;

        sub-video*)
            echo_console "stop cam_app service for $1 : "

            start-stop-daemon -K -p /var/run/$1.pid

            if [ $? = 0 ]; then
                echo_console "OK\n"

                # wait for process exit
                while true
                do
                    if [ -d /proc/$(cat /var/run/$1.pid) ]; then
                        sleep 0.2
                        let count+=1
                    else
                        break
                    fi
                    # time out 3s, then send kill signal to process
                    if [ $count -gt $TIME_OUT_CNT ]; then
                        kill -9 $(cat /var/run/$1.pid)
                        sleep 0.5
                    fi
                done

            else
                echo_console "FAIL\n"
            fi

            [ -f $TMP_VERSION_FILE ] && {
                jq -cMS 'del(.sub_cam)' $TMP_VERSION_FILE > $TMP_VERSION_FILE.tmp && \
                mv $TMP_VERSION_FILE.tmp $TMP_VERSION_FILE && sync
                [ $(jq -e 'has("main_cam")' $TMP_VERSION_FILE) = "true" ] || rm -rf $TMP_VERSION_FILE
            }
    esac
}

reload_uvc()
{
    [ -d /dev/v4l/by-id ] && {
        DEVS=$(ls /dev/v4l/by-id)
        if [ "x$DEVS" != "x" ]; then
            for dev in $DEVS
            do
                stop_uvc $dev
                start_uvc $dev
            done
        fi
    }
}

stop_all_uvc()
{
    [ -d /dev/v4l/by-id ] && {
        DEVS=$(ls /dev/v4l/by-id)
        if [ "x$DEVS" != "x" ]; then
            for dev in $DEVS
            do
                stop_uvc $dev
            done
        fi
    }
}

#echo_console "MDEV=$MDEV ; ACTION=$ACTION ; DEVPATH=$DEVPATH ;\n"

#sync && echo 3 > /proc/sys/vm/drop_caches

case "${ACTION}" in
add)
        start_uvc ${MDEV}
        ;;
remove)
        stop_uvc ${MDEV}
        ;;
# cmd: ACTION=reload /usr/bin/auto_uvc.sh
reload)
        reload_uvc
        ;;
# cmd: ACTION=stop /usr/bin/auto_uvc.sh
stop)
        stop_all_uvc
        ;;
esac

exit 0
