#!/bin/bash

### BEGIN INIT INFO
# Provides:       tuning
# Required-Start:    $local_fs $remote_fs $network $syslog $named
# Required-Stop:     $local_fs $remote_fs $network $syslog $named
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: tunes CPU performance and process affinity
# Description:       tunes CPU performance and process affinity
### END INIT INFO

PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
TASKSET=/usr/bin/taskset
RENICE=/usr/bin/renice
CPUFREQSET=/usr/bin/cpufreq-set
CPUFREQINFO=/usr/bin/cpufreq-info

test -x $TASKSET || exit 0
test -x $RENICE || exit 0

set_irq_affinity() {
        TTYS4=`grep ttyS4 < /proc/interrupts | awk -F: '{print $1}' | tr -d '[:blank:]'`
        # TTYS2=`grep ttyS2 < /proc/interrupts | awk -F: '{print $1}' | tr -d '[:blank:]'`
        USB2=`grep "hcd:usb2" < /proc/interrupts | awk -F: '{print $1}' | tr -d '[:blank:]'`
        USB3=`grep "hcd:usb3" < /proc/interrupts | awk -F: '{print $1}' | tr -d '[:blank:]'`
        # USB4=`grep "hcd:usb4" < /proc/interrupts | awk -F: '{print $1}' | tr -d '[:blank:]'`

        # Split MCU IRQs evenly between CPUs 1 and 2
        echo 4 > /proc/irq/${TTYS4}/smp_affinity
        # echo 2 > /proc/irq/${TTYS2}/smp_affinity
        echo 8 > /proc/irq/${USB2}/smp_affinity
        echo 8 > /proc/irq/${USB3}/smp_affinity
        # echo 8 > /proc/irq/${USB4}/smp_affinity
}

set_affinity() {
        AFFINITY_CPUS=$1
        COMMAND_NAME=$2

        echo "Setting CPU affinity for all $COMMAND_NAME threads to CPU $AFFINITY_CPUS"
        for p in `ps -eLf | grep -i $COMMAND_NAME | grep -v grep | awk '{print $4}'`
        do
                $TASKSET -cp $AFFINITY_CPUS $p
        done
}

make_nice() {
        NICE_LEVEL=$1
        COMMAND_NAME=$2

        echo "Setting all $COMMAND_NAME threads to niceness level $NICE_LEVEL"
        for p in `ps -eLf | grep $COMMAND_NAME | grep -v grep | awk '{print $4}'`
        do
                $RENICE $NICE_LEVEL $p
        done
}

tune_up() {
        # Put CPU into Performance Mode
        # echo "Placing Printer CPU into Performance mode with fixed frequency"
        # $CPUFREQSET -g performance
        # $CPUFREQSET -d 1.10Ghz
        # $CPUFREQINFO

        # Isolate Klipper from client and mjpg-streamer
        echo "\nIsolating klipper from qd-max4-client, ustreamer, moonraker, and nginx\n"
        set_affinity 0 /home/qidi/QIDI_Client/bin/client
        set_affinity 0 ustreamer
        set_affinity 0 nginx
        set_affinity 0 moonraker
        set_affinity 1-2 klippy

        set_irq_affinity

        # Set Niceness of qd-max4-client and ustreamer
        echo "\nSetting niceness of qd-max4-client, ustreamer, nginx\n"
        make_nice 1 /home/qidi/QIDI_Client/bin/client
        make_nice 1 nginx
        make_nice 2 ustreamer
}

tune_down() {
        # Put CPU into ondemand Mode
        # echo "Placing Printer CPU into default ondemand and frequency scaling mode"
        # $CPUFREQSET -g ondemand
        # $CPUFREQSET -d 408Mhz
        # $CPUFREQINFO

        # Unisolate Klipper, client, nginx, moonraker, and ustreamer
        echo "Resetting klipper/ustreamer/client/nginx/moonraker tuning to system default"
        set_affinity 0-3 /home/qidi/QIDI_Client/bin/client
        set_affinity 0-3 klippy
        set_affinity 0-3 ustreamer
        set_affinity 0-3 nginx
        set_affinity 0-3 moonraker

        echo "Resetting niceness of qd-max4-client, ustreamer, nginx to system default"
        make_nice 0 /home/qidi/QIDI_Client/bin/client
        make_nice 0 ustreamer
        make_nice 0 nginx
}

case "$1" in
        start)
                sleep 30        # Wait for all the main printer processes to start up
                tune_up
                ;;

        reload)
                tune_up
                ;;
        stop)
                tune_down
                ;;
        *)
                exit 0
                ;;
esac

exit 0