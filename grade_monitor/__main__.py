"""
CLI 入口：一次性模式 / 循环模式。

用法：
    python -m grade_monitor          # 跑一遍所有用户后退出
    python -m grade_monitor loop     # 循环模式（长驻进程）
"""

import os
import signal
import sys
import time
from datetime import datetime

from .config import load_config, load_users
from .logging_config import log
from .monitor import process_user

# ── 服务启动时间 & 轮次计数器 ──
_start_time: float | None = None
_round_count: int = 0


def _handle_signal(signum: int, _frame) -> None:
    """处理 SIGTERM / SIGINT，记录停止事件后退出。"""
    sig_name = signal.Signals(signum).name
    uptime = ""
    if _start_time is not None:
        elapsed = time.time() - _start_time
        hours, remainder = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime = f"，已运行 {hours}h{minutes}m{seconds}s"
    log.info(f"🛑 [服务停止] 收到 {sig_name} 信号{uptime}，进程退出 (PID {os.getpid()})")
    sys.exit(0)


def run_once() -> None:
    """跑一遍所有用户后退出。供 launchd StartInterval 调用。"""
    cfg = load_config()
    users = load_users(cfg)
    log.info(f"--- 第 {_round_count} 轮查询开始 (PID {os.getpid()}, {len(users)} 用户) ---")
    for user in users:
        try:
            process_user(user)
        except Exception as e:
            log.error(
                f"[{user.get('name')}] 顶层异常: {type(e).__name__}: {e}",
                exc_info=True,
            )
    log.info(f"--- 第 {_round_count} 轮查询结束 ---")


def run_loop() -> None:
    """循环模式：每轮重读 config，改 interval_minutes 即刻生效。"""
    global _start_time, _round_count
    _start_time = time.time()

    # 注册信号处理（launchctl unload 发 SIGTERM）
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info(f"🚀 [服务启动] 成绩监控服务已启动 (PID {os.getpid()})")

    while True:
        _round_count += 1
        try:
            run_once()
        except KeyboardInterrupt:
            _handle_signal(signal.SIGINT, None)
            return
        except Exception as e:
            log.error(f"循环异常: {type(e).__name__}: {e}", exc_info=True)
        try:
            interval = int(load_config().get("interval_minutes", 20)) * 60
        except Exception as e:
            log.warning(f"读取 interval_minutes 失败，回退 20 分钟: {e}")
            interval = 1200
        log.info(f"等待 {interval // 60} 分钟后再跑一轮...")
        time.sleep(interval)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        run_loop()
    else:
        run_once()


if __name__ == "__main__":
    main()

