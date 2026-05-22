#!/bin/bash
# 重启成绩监控（修改配置或代码后用这个）
PLIST=~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist
DIR="$(dirname "$0")"

launchctl unload "$PLIST" 2>/dev/null
sleep 1
launchctl load "$PLIST"
sleep 2

if launchctl list | grep -q gradeMonitor; then
    echo "🔄 成绩监控已重启"
    launchctl list | grep gradeMonitor
else
    echo "❌ 重启失败"
    exit 1
fi
