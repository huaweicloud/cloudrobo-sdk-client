#!/bin/bash
# A1Z SocketCAN 初始化脚本
# 用于 HHS USB-CANFD Pro-II 适配器 (VID:PID = a8fa:8598)
# 每次开机后执行一次，或加入 systemd 服务自动启动
#
# 用法:
#   bash scripts/setup_can.sh          # 默认 can0
#   bash scripts/setup_can.sh can0     # 指定 can0
#   bash scripts/setup_can.sh can1     # 指定 can1 (双臂第二路)

set -e

CAN_IF="${1:-can0}"

echo "=== A1Z SocketCAN 初始化 ==="
echo "目标接口: $CAN_IF"

# 1. 加载 gs_usb 内核模块
echo "[1/4] 加载 gs_usb 驱动..."
sudo modprobe gs_usb

# 2. 绑定 HHS 适配器到 gs_usb 驱动
echo "[2/4] 绑定 HHS CANFD 适配器 (a8fa:8598)..."
sudo sh -c 'echo "a8fa 8598" > /sys/bus/usb/drivers/gs_usb/new_id' 2>/dev/null || true

# 3. 关闭已有接口 (如果存在)
echo "[3/4] 重置接口..."
sudo ip link set "$CAN_IF" down 2>/dev/null || true

# 4. 配置并启动 CAN 接口 (1 Mbps)
echo "[4/4] 配置 $CAN_IF @ 1Mbps..."
sudo ip link set "$CAN_IF" type can bitrate 1000000
sudo ip link set "$CAN_IF" txqueuelen 1000
sudo ip link set "$CAN_IF" up

echo ""
echo "SocketCAN ($CAN_IF) 已就绪。"
echo "验证: candump $CAN_IF"
echo "状态: ip -details link show $CAN_IF"
