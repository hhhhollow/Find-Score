#!/bin/bash
# 查看成绩监控状态
echo "=== LaunchAgent 状态 ==="
if launchctl list | grep -q gradeMonitor; then
    launchctl list | grep gradeMonitor
    echo "✅ 已加载（一次性模式：每 20 分钟由 launchd 触发；睡眠时不跑）"
else
    echo "🛑 未加载"
fi

echo ""
echo "=== 当前是否有 Python 进程在跑 ==="
ps aux | grep grade_monitor.py | grep -v grep || echo "（无 — 一次性模式空闲时本应没有进程）"

echo ""
echo "=== 最近 15 行日志 ==="
tail -15 "$(dirname "$0")/grade_monitor.log" 2>/dev/null || echo "（暂无日志）"
