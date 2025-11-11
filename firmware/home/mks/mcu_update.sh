#!/bin/bash
# QD-Q2/MAX4-MCU

VERSION=1.6 # pattern compatible
RESET_CMD=73 # 0x73 -> 115
KLIPPER_BIN="/home/mks/klipper.bin"
KLIPPER_MCU="/dev/serial/by-id/usb-Klipper_QIDI_MAIN_*"
KLIPPER_MCU_OLD="/dev/serial/by-id/usb-Klipper_stm32f407xx_*"
BOOTLOADER_MCU="/dev/serial/by-id/usb-*_DRIVER_BOOT*"
BOOTLOADER_MCU_OLD="/dev/serial/by-id/usb-*-CDC_*"


echo "Start mcu_update.sh, version: $VERSION"

if [ $# -ge 1 ]
then
	KLIPPER_BIN=$1
	echo "KLIPPER_BIN: $KLIPPER_BIN"
fi
if [ $# -ge 2 ]
then
	KLIPPER_MCU=$2
	echo "KLIPPER_MCU: $KLIPPER_MCU"
fi
if [ $# -ge 3 ]
then
	BOOTLOADER_MCU=$3
	echo "BOOTLOADER_MCU: $BOOTLOADER_MCU"
fi
if [ $# -ge 4 ]
then
	RESET_CMD=$4
	echo "RESET_CMD: $RESET_CMD"
fi

if [ -f $KLIPPER_BIN ]
then
	# reset & wait success
	# /home/mks/serial_com $KLIPPER_MCU --cmd=$RESET_CMD
	if [ -e $KLIPPER_MCU ] # softlink
	then
		# get dict
		/home/mks/serial_com_dict $KLIPPER_MCU
		/home/mks/zlib_decompress
		msgid=$(/home/mks/json_reader dict.json | awk -F': ' '{print $2}')
		if [ $msgid -le 0 ]
		then
			msgid=$((128+$msgid))
		fi
		RESET_CMD=$msgid
		echo "Get $KLIPPER_MCU 's dict, reset commond id = $msgid."
	elif [ -e $KLIPPER_MCU_OLD ]
	then
		# get dict
		/home/mks/serial_com_dict $KLIPPER_MCU_OLD
		/home/mks/zlib_decompress
		msgid=$(/home/mks/json_reader dict.json | awk -F': ' '{print $2}')
		if [ $msgid -le 0 ]
		then
			msgid=$((128+$msgid))
		fi
		RESET_CMD=$msgid
		echo "Get $KLIPPER_MCU_OLD 's dict, reset commond id = $msgid."
	fi

	ready=0
	cnt=1
	while [ $cnt -le 5 ]
	do
		if [ -e $KLIPPER_MCU ] # softlink
		then
			echo "Find $KLIPPER_MCU in loop $cnt, which means mcu has still not reset..."
			/home/mks/serial_com $KLIPPER_MCU --cmd=$RESET_CMD
			sleep 2
		elif [ -e $KLIPPER_MCU_OLD ]
		then
			echo "Find $KLIPPER_MCU_OLD in loop $cnt, which means mcu has still not reset..."
			/home/mks/serial_com $KLIPPER_MCU_OLD --cmd=$RESET_CMD
			sleep 2
		else
			echo "No Klipper MCU[$KLIPPER_MCU] exist in loop $cnt, then goto check bootloader-mcu"
			ready=1
			break
		fi
		cnt=$(($cnt+1))
	done
	if [ $ready -eq 1 ]
	then
		echo "Waiting for bootloader-mcu"
	else
		echo "ERROR: mcu is always being a Klipper MCU, reset failed..."
		result=2 # temp
		exit $result
	fi

	# check whether a bootloader device exists 
	exist=0
	cnt=1
	while [ $cnt -le 10 ]
	do
		if [ -e $BOOTLOADER_MCU ]
		then
			echo "Find $BOOTLOADER_MCU in loop $cnt"
			exist=1
			break
		elif [ -e $BOOTLOADER_MCU_OLD ]
		then
			echo "Find $BOOTLOADER_MCU_OLD in loop $cnt"
			BOOTLOADER_MCU=$BOOTLOADER_MCU_OLD
			echo "Set bootloader device as $BOOTLOADER_MCU"
			exist=1
			break
		else
			echo "Warning: Can't find $BOOTLOADER_MCU in loop $cnt..."
			sleep 1
		fi
		cnt=$(($cnt+1))
	done
	
	if [ $exist -eq 1 ]
	then
		# update
		/home/mks/hid-flash $KLIPPER_BIN $BOOTLOADER_MCU

		cmd=$?
		if [ $cmd -eq 0 ]
		then
			result=0
		else
			echo "ERROR: hid-flash failed($cmd)..."
			result=$((3+$cmd))
		fi
	else
		echo "ERROR: no ACM-CDC device..."
		result=2
	fi

	# systemctl start klipper.service
	exit $result
else
	echo "ERROR: no firmware..."
	exit 1
fi
