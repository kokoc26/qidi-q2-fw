#!/bin/bash

echo "Start QD_Max4-client $(date "+%Y%m%d%H%M%S")"
# taskset -c 0 /home/qidi/QIDI_Client/bin/qidiclient

# 1. 确保当前 Shell 及其子进程允许生成 Core
ulimit -c unlimited

# 2. 使用 exec 结合 taskset
# exec 会让 taskset 替换掉 Bash 进程
# taskset 随后会运行 qidiclient，最终 qidiclient 会占据这个 PID
# 这样产生的 Core 文件其“指纹”就绝对是 qidiclient 的了
exec taskset -c 0 /home/qidi/QIDI_Client/bin/qidiclient

