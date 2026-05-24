"""
CLI 入口：一次性模式 / 循环模式。

用法：
    python -m grade_monitor          # 跑一遍所有用户后退出
    python -m grade_monitor loop     # 循环模式（长驻进程）
"""

import os
import sys
import time

from .config import load_config, load_users
from .logging_config import log
from .monitor import process_user


def run_once() -> None:
    """跑一遍所有用户后退出。供 launchd StartInterval 调用。"""
    cfg = load_config()
    users = load_users(cfg)
    log.info(f"=== 成绩监控启动 (PID {os.getpid()}, {len(users)} 用户) ===")
    for user in users:
        try:
            process_user(user)
        except Exception as e:
            log.error(
                f"[{user.get('name')}] 顶层异常: {type(e).__name__}: {e}",
                exc_info=True,
            )
    log.info("=== 本轮结束 ===")


def run_loop() -> None:
    """循环模式：每轮重读 config，改 interval_minutes 即刻生效。"""
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，退出")
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
