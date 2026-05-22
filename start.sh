#!/bin/bash
# 启动成绩监控（手动启动；不会开机自启）
PLIST=~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist
LABEL=com.hhhhollow.gradeMonitor

# 已经在运行？
if pgrep -f grade_monitor.py >/dev/null; then
    echo "⚠️  成绩监控已经在运行了"
    launchctl list | grep gradeMonitor
    exit 0
fi

# 没加载就加载（RunAtLoad=false，所以加载不会自动跑）
if ! launchctl list | grep -q "$LABEL"; then
    launchctl load "$PLIST"
fi

# 显式启动
launchctl start "$LABEL"
sleep 2

if pgrep -f grade_monitor.py >/dev/null; then
    echo "✅ 成绩监控已启动"
    launchctl list | grep gradeMonitor
else
    echo "❌ 启动失败，请检查日志: tail launchd.stderr.log"
    exit 1
fi
