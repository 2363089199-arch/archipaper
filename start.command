#!/bin/bash
# ArchiPaper 服务器启动脚本
# 双击此文件即可启动，或终端运行: bash start.command

cd "$(dirname "$0")"

# 获取本机局域网 IP
IP=$(ipconfig getifaddr en0 2>/dev/null || echo "未连接WiFi")

echo "╔══════════════════════════════════════╗"
echo "║       ArchiPaper Server             ║"
echo "╠══════════════════════════════════════╣"
echo "║  本机访问:                           ║"
echo "║  http://localhost:8080               ║"
if [ "$IP" != "未连接WiFi" ]; then
echo "║                                      ║"
echo "║  其他设备访问:                        ║"
echo "║  http://$IP:8080          ║"
fi
echo "║                                      ║"
echo "║  按 Ctrl+C 停止服务器                 ║"
echo "╚══════════════════════════════════════╝"
echo ""

python3 -m http.server 8080
