#!/bin/bash
# 查看成绩监控状态
echo "=== LaunchAgent 状态 ==="
if launchctl list | grep -q gradeMonitor; then
    launchctl list | grep gradeMonitor
    echo "✅ 正在运行"
else
    echo "🛑 未运行"
fi

echo ""
echo "=== 进程信息 ==="
ps aux | grep grade_monitor.py | grep -v grep || echo "（无进程）"

echo ""
echo "=== 最近 10 行日志 ==="
tail -10 "$(dirname "$0")/grade_monitor.log" 2>/dev/null || echo "（暂无日志）"
