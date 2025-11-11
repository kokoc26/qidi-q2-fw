#! /bin/bash
# Temp for multi-mcu, force set mcu as usb-Klipper_QIDI_MAIN
KLIPPER_MCU="/dev/serial/by-id/usb-Klipper_QIDI_MAIN_*"
KLIPPER_MCU_OLD="/dev/serial/by-id/usb-Klipper_stm32f407xx_*"

echo "$(date +%Y-%m-%d_%H-%M-%S)" > /root/debug_mcu-id.log
while [ ! -d /dev/serial/by-id ]; do
	echo "no id" >> /root/debug_mcu-id.log
	sleep 1
done

if [ -d "/dev/serial/by-id" ];then
	while [ "$(ls -A $KLIPPER_MCU)" == "" ] && [ "$(ls -A $KLIPPER_MCU_OLD)" == "" ]
	do
		echo "no ready mcu as KLIPPER_MCU" >> /root/debug_mcu-id.log
		sleep 1
	done

	if [ -e $KLIPPER_MCU ] # softlink
	then
		path=$(ls $KLIPPER_MCU)
	elif [ -e $KLIPPER_MCU_OLD ]
	then
		path=$(ls $KLIPPER_MCU_OLD)
	fi
	echo "get id info :$path" >> /root/debug_mcu-id.log

	if [ -f "/home/mks/printer_data/config/MCU_ID.cfg" ]; then
		content=$(cat /home/mks/printer_data/config/MCU_ID.cfg)
		serial_content=$(echo "$content" | grep 'serial:' | awk -F'serial:' '{print $2}')
		if [[ -z "$serial_content" ]]; then
			echo "empty cfg serial:" >> /root/debug_mcu-id.log
			sed -i "s|serial:.*|serial:"${path}"|g" /home/mks/printer_data/config/MCU_ID.cfg
			echo "set id finish" >> /root/debug_mcu-id.log
		else
			echo "MCU_ID.cfg serial: $serial_content" >> /root/debug_mcu-id.log
			if [ "$path" == "$serial_content" ]; then
				echo "nothing need to do with MCU_ID.cfg" >> /root/debug_mcu-id.log
			else
				echo "need to update MCU_ID.cfg" >> /root/debug_mcu-id.log
				sed -i "s|serial:.*|serial:"${path}"|g" /home/mks/printer_data/config/MCU_ID.cfg
				echo "set id finish" >> /root/debug_mcu-id.log
			fi
		fi
	else
		touch /home/mks/printer_data/config/MCU_ID.cfg
		echo '[mcu]' > /home/mks/printer_data/config/MCU_ID.cfg
		echo 'serial:' >> /home/mks/printer_data/config/MCU_ID.cfg
		echo 'restart_method: command' >> /home/mks/printer_data/config/MCU_ID.cfg
		sed -i "s|serial:.*|serial:"${path}"|g" /home/mks/printer_data/config/MCU_ID.cfg
		echo "set id finish" >> /root/debug_mcu-id.log
	fi
fi

# 删除多余的printer-* 冗余文件
if [ -d "/home/mks/printer_data/config" ];then
        rm /home/mks/printer_data/config/printer-*.cfg -f
fi

# 删除多余的其他信息
if [ -d "/home/mks/printer_data/logs" ]; then
		cd "/home/mks/printer_data/logs" && \
    	ls -1 klippy.log.* 2>/dev/null | sort -r | tail -n +3 | xargs -r rm -f

# 删除ui多余日志信息
if [ -d "/root/makerbase-client" ]; then
		cd "/root/makerbase-client" && \
    	ls mksclient_*.log 2>/dev/null | awk -F'[_.]' '{print $2, $0}' | sort -r | awk 'NR>5 {print $2}' | xargs -r rm -f --

#	rm /home/mks/klipper_logs/klippy.log.* -f
fi

