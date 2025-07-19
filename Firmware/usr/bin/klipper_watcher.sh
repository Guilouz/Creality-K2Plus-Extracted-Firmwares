#!/bin/sh

local timeout=30

while [ ! -f /var/run/klippy.pid ] && [ $timeout -gt 0 ]; do
    sleep 1
    timeout=$((timeout - 1))
done

PID=$(ubus call service list | jsonfilter -e '$.klipper.instances.*.pid')
echo -500 > /proc/$PID/oom_score_adj
