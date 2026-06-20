#!/bin/bash
# 停止成绩监控（卸载 LaunchAgent，关闭开机自启）
LOG_FILE="$(dirname "$0")/grade_monitor.log"
PLIST=~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist

if ! launchctl list | grep -q gradeMonitor; then
    echo "⚠️  成绩监控本来就没运行"
    exit 0
fi

launchctl unload "$PLIST"
sleep 1

if launchctl list | grep -q gradeMonitor; then
    echo "❌ 停止失败"
    exit 1
else
    echo "🛑 成绩监控已停止"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] [shell] 用户执行 stop.sh — 服务已停止" >> "$LOG_FILE"
fi
