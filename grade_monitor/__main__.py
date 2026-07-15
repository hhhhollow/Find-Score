"""CLI 入口：一次性模式与循环模式。"""

import argparse
import os
import signal
import sys
import time
from collections.abc import Sequence

from .config import ConfigError, DEFAULT_INTERVAL_MINUTES, load_config
from .locking import InstanceAlreadyRunning, instance_lock
from .logging_config import configure_logging, log
from .monitor import process_user
from .rounds import next_round_number

_start_time: float | None = None


def _handle_signal(signum: int, _frame) -> None:
    sig_name = signal.Signals(signum).name
    uptime = ""
    if _start_time is not None:
        elapsed = time.time() - _start_time
        hours, remainder = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime = f"，已运行 {hours}h{minutes}m{seconds}s"
    log.info(
        f"🛑 [服务停止] 收到 {sig_name} 信号{uptime}，"
        f"进程退出 (PID {os.getpid()})",
    )
    raise SystemExit(0)


def _run_round(users: list[dict], round_number: int) -> bool:
    log.info(
        f"--- 第 {round_number} 轮查询开始 "
        f"(PID {os.getpid()}, {len(users)} 用户) ---",
    )
    all_succeeded = True
    for index, user in enumerate(users):
        try:
            if not process_user(user, migrate_legacy=len(users) == 1 and index == 0):
                all_succeeded = False
        except Exception as error:
            all_succeeded = False
            log.error(
                f"[{user.get('name')}] 顶层异常: {type(error).__name__}: {error}",
                exc_info=True,
            )
    log.info(f"--- 第 {round_number} 轮查询结束 ---")
    return all_succeeded


def run_once() -> bool:
    """加载一次配置，跑完所有用户后退出。"""
    cfg = load_config()
    return _run_round(cfg["users"], next_round_number())


def _sleep_until(deadline: float, max_chunk_seconds: float = 30) -> None:
    """用墙上时间分段等待，休眠唤醒后尽快补查。"""
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, max_chunk_seconds))


def run_loop() -> None:
    """每轮重读配置，配置变更在下一轮生效。"""
    global _start_time
    _start_time = time.time()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    log.info(f"🚀 [服务启动] 成绩监控服务已启动 (PID {os.getpid()})")

    while True:
        interval_seconds = DEFAULT_INTERVAL_MINUTES * 60
        try:
            cfg = load_config()
            _run_round(cfg["users"], next_round_number())
            interval_seconds = cfg["interval_minutes"] * 60
        except Exception as error:
            log.error(f"循环异常: {type(error).__name__}: {error}", exc_info=True)

        log.info(f"等待 {interval_seconds // 60} 分钟后再跑一轮...")
        _sleep_until(time.time() + interval_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    # 配置、cookies、缓存和日志均默认仅当前用户可读写。
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="BISTU 成绩监控")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("once", "loop"),
        default="once",
        help="once 跑一轮后退出；loop 持续轮询",
    )
    args = parser.parse_args(argv)
    configure_logging()
    try:
        with instance_lock():
            if args.mode == "loop":
                run_loop()
                return 0
            return 0 if run_once() else 1
    except InstanceAlreadyRunning as error:
        log.error(str(error))
        return 2
    except (ConfigError, OSError) as error:
        log.error(f"启动失败: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
