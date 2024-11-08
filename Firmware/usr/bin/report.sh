#!/bin/sh

PROG=/usr/bin/log_main
PID_FILE=/var/run/log_main.pid

start() {
    start-stop-daemon -S -b -m -p $PID_FILE \
        --exec $PROG
    [ $? = 0 ] && echo "OK" || echo "FAIL"
}

stop() {
    start-stop-daemon -K -p $PID_FILE
    [ $? = 0 ] && echo "OK" || echo "FAIL"
}

restart() {
    stop
    sleep 1
    start
}

case "$1" in
    start)
        [ "x$(ps | grep '/usr/bin/log_main' | grep -v grep)" != "x" ] && echo "a process running" && exit 1
        start
    ;;
    stop)
        stop
    ;;
    restart)
        restart
    ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
        exit 1
esac
