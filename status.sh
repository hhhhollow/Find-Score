#!/bin/bash
# 查看成绩监控状态
echo "=== LaunchAgent 状态 ==="
DIR="$(dirname "$0")"
if launchctl list | grep -q gradeMonitor; then
    launchctl list | grep gradeMonitor
    INTERVAL=$(python3 -c "import json; print(json.load(open('$DIR/config.json')).get('interval_minutes', 20))" 2>/dev/null || echo "?")
    echo "✅ 已加载（长驻 loop 模式：每 ${INTERVAL} 分钟一轮；改 config.json 后下一轮即生效）"
else
    echo "🛑 未加载"
fi

echo ""
echo "=== 当前是否有 Python 进程在跑 ==="
ps aux | grep grade_monitor.py | grep -v grep || echo "（无 — 长驻模式下应该有一个 python 进程）"

echo ""
echo "=== 最近 15 行日志 ==="
tail -15 "$(dirname "$0")/grade_monitor.log" 2>/dev/null || echo "（暂无日志）"
