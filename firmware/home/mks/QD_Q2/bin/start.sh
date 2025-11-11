#!/bin/bash

echo "Start QD_Q2-client $(date "+%Y%m%d%H%M%S")"

# # /home/mks/QD_Q2/bin/client >/dev/null 2>&1
# /home/mks/QD_Q2/bin/client

taskset -c 0 /home/mks/QD_Q2/bin/client
