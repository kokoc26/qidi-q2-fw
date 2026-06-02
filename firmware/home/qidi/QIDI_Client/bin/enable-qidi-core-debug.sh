#!/bin/bash
set -euo pipefail

SERVICE="qidi-client.service"
CORE_DIR="/root/qidi-client-coredumps"
USER_NAME="root"
GROUP_NAME="root"

# 固定单文件名（始终覆盖）
CORE_FILENAME="core.qidi-client"

SYSCTL_FILE="/etc/sysctl.d/99-qidi-core.conf"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE}.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/core.conf"

echo "=== [1/5] 创建 core 目录 ==="
mkdir -p "$CORE_DIR"
chown "$USER_NAME:$GROUP_NAME" "$CORE_DIR"
chmod 0700 "$CORE_DIR"

echo
echo "=== [2/5] 设置内核 core_pattern（单文件覆盖模式） ==="
# 不带任何 %p %t，始终覆盖同一个文件
CORE_PATTERN="${CORE_DIR}/${CORE_FILENAME}"
cat > "$SYSCTL_FILE" <<EOF
# qidi-client core dump (global kernel setting)
kernel.core_pattern=${CORE_PATTERN}
kernel.core_uses_pid=0
EOF
sysctl -p "$SYSCTL_FILE"
echo "core_pattern = $CORE_PATTERN"
echo "core_uses_pid = 0 (单文件覆盖)"

echo
echo "=== [3/5] systemd 开启 qidi-client core ==="
mkdir -p "$OVERRIDE_DIR"
cat > "$OVERRIDE_FILE" <<EOF
[Service]
LimitCORE=infinity
PrivateTmp=no
EOF
echo "已写入 $OVERRIDE_FILE"

echo
echo "=== [4/5] 清理旧 core 文件（只保留一个） ==="
# 主动删除可能存在的旧 core 变体
rm -f "${CORE_DIR}"/core.*
echo "仅保留: ${CORE_PATTERN}"

echo
echo "=== [5/5] 重载 systemd 并重启服务 ==="
systemctl daemon-reexec
systemctl daemon-reload
# systemctl restart "$SERVICE"
echo
echo "✅ Core dump 已启用（单文件覆盖模式）"
echo "📌 Core 文件固定为: ${CORE_PATTERN}"
echo "⚠ 注意：该 core_pattern 为内核全局设置"