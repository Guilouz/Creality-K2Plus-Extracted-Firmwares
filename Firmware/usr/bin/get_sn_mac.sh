#!/bin/sh

if [ ! $# -eq 1 ]; then
	#echo "without parameter(sn or mac)."
	exit 1
fi

USRDATA=/mnt/UDISK
RANDOM=/proc/sys/kernel/random/uuid

# 传参转为小写
PARAM=$(echo $1 | tr 'A-Z' 'a-z')

# 通过keybox去获取安全key内容
KEYBOX_SCRIPT=/usr/bin/keybox

# sn
SN=$($KEYBOX_SCRIPT -r sn | grep 'sn = ' | awk -F'sn = ' '{print $NF}')
# mac
MAC=$($KEYBOX_SCRIPT -r wifi_mac | grep 'wifi_mac = ' | awk -F'wifi_mac = ' '{print $NF}')
# model
MODEL=$($KEYBOX_SCRIPT -r model | grep 'model = ' | awk -F'model = ' '{print $NF}')
# board
BOARD=$($KEYBOX_SCRIPT -r board | grep 'board = ' | awk -F'board = ' '{print $NF}')
# pcba_test
PCBA_TEST=$($KEYBOX_SCRIPT -r pcba_test | grep 'pcba_test = ' | awk -F'pcba_test = ' '{print $NF}')
# machine sn
MACHINE_SN=$($KEYBOX_SCRIPT -r machine_sn | grep 'machine_sn = ' | awk -F'machine_sn = ' '{print $NF}')
# structure version
STRUCTURE_VERSION=$($KEYBOX_SCRIPT -r struct_ver | grep 'struct_ver = ' | awk -F'struct_ver = ' '{print $NF}')
# factory stress test
STRESS_TEST=$($KEYBOX_SCRIPT -r stress_test | grep 'stress_test = ' | awk -F'stress_test = ' '{print $NF}')

# sn校验 -- 长度14，由0-9a-fA-F组成
check_sn()
{
	local result=$(echo $1 | sed -n '/^[0-9A-Fa-f]\{14\}$/ p')
	#echo "result:${result}"
	if [ "${result}" = "" ]; then
	#echo "sn is invalid"
		return 1
	fi
	return 0
}

# mac校验 -- 长度12，由0-9a-fA-F组成
check_mac()
{
	local result=$(echo $1 | sed -n '/^[0-9A-Fa-f]\{12\}$/ p')
	local macaddr=
	#echo "result:${result}"
	if [ "${result}" = "" ]; then
		#echo "mac is invalid"
		return 1
	fi
	return 0
}

if [ $PARAM = "sn" ]; then
	# 获取序列号
	check_sn ${SN}
	if [ $? != 0 ]; then
		echo "00000000000000"
		exit 1
	fi
	echo ${SN}
elif [ $PARAM = "mac" ]; then
	# 获取MAC地址
	check_mac ${MAC}
	if [ $? != 0 ]; then
		# 从macaddr.txt中获取
		if [ -f ${USRDATA}/macaddr.txt ]; then
			MAC=$(cat ${USRDATA}/macaddr.txt | sed 's/[^0-9|a-f|A-F]//g')
			if [ "${#MAC}" = "12" ]; then
				echo ${MAC}
				exit 0
			fi
		fi
		# 从随机数中获取
		if [ -f ${RANDOM} ]; then
			MAC=$(cat ${RANDOM} | sed 's/[^0-9|a-f|A-F]//g')
			MAC=d03110${MAC:0:6}
			echo ${MAC}
			exit 0
		fi
	fi
	echo ${MAC}
elif [ $PARAM = "model" ]; then
	echo ${MODEL}
elif [ $PARAM = "board" ]; then
	echo ${BOARD}
elif [ $PARAM = "pcba_test" ]; then
    if [ "x$PCBA_TEST" = "x1" ]; then
       echo "1"
    else
       echo "0"
    fi
elif [ $PARAM = "machine_sn" ]; then
    if [ "x$MACHINE_SN" != "x" ]; then
       echo "$MACHINE_SN"
    else
       echo "0"
    fi
elif [ $PARAM = "structure_version" ]; then
    if [ "x$STRUCTURE_VERSION" != "x" ]; then
       echo "$STRUCTURE_VERSION"
    else
       echo "0"
    fi
elif [ $PARAM = "stress_test" ]; then
    if [ "x$STRESS_TEST" != "x" ]; then
       echo "$STRESS_TEST"
    else
       echo "0"
    fi
else
	exit 1
fi
