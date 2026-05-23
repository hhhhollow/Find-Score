#!/bin/bash
# 加载 LaunchAgent；launchd 立刻跑一次，之后按 plist 中 StartInterval 定时（睡眠期间不跑）
PLIST=~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist
LABEL=com.hhhhollow.gradeMonitor

if launchctl list | grep -q "$LABEL"; then
    echo "⚠️  成绩监控已加载，立刻触发一次查询..."
    launchctl kickstart "gui/$(id -u)/$LABEL"
    exit 0
fi

launchctl load "$PLIST"
sleep 2

if launchctl list | grep -q "$LABEL"; then
    echo "✅ 成绩监控已加载（launchd 每 20 分钟触发一次；合盖睡眠期间不跑）"
    launchctl list | grep gradeMonitor
else
    echo "❌ 加载失败，请检查日志: tail launchd.stderr.log"
    exit 1
fi
