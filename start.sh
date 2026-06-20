#!/bin/bash
# 加载 LaunchAgent；脚本自己按 config.json 的 interval_minutes 长驻循环
LOG_FILE="$(dirname "$0")/grade_monitor.log"
PLIST=~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist
LABEL=com.hhhhollow.gradeMonitor
DIR="$(dirname "$0")"

if launchctl list | grep -q "$LABEL"; then
    echo "⚠️  成绩监控已加载，立刻触发一次查询..."
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] [shell] 用户执行 start.sh — 服务已在运行，触发 kickstart" >> "$LOG_FILE"
    launchctl kickstart "gui/$(id -u)/$LABEL"
    exit 0
fi

launchctl load "$PLIST"
sleep 2

if launchctl list | grep -q "$LABEL"; then
    INTERVAL=$(python3 -c "import json; print(json.load(open('$DIR/config.json')).get('interval_minutes', 20))" 2>/dev/null || echo "?")
    echo "✅ 成绩监控已加载（长驻 loop 模式：每 ${INTERVAL} 分钟一轮；改 config.json 即生效）"
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] [shell] 用户执行 start.sh — 服务首次启动成功" >> "$LOG_FILE"
    launchctl list | grep gradeMonitor
else
    echo "❌ 加载失败，请检查日志: tail launchd.stderr.log"
    exit 1
fi
