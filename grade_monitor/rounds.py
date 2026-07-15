"""跨进程持久化查询轮次。"""

import json
import re
from pathlib import Path
from typing import Any

from .constants import LOG_FILE, MONITOR_STATE_FILE
from .logging_config import log
from .storage import atomic_write_json

STATE_VERSION = 1
_ROUND_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[INFO\] --- 第 (\d+) 轮查询开始 \(PID \d+, \d+ 用户\) ---$",
)


def _last_logged_round(log_file: Path) -> int:
    """首次启用状态文件时，从现有滚动日志延续最近的轮次。"""
    latest: tuple[str, int] | None = None
    for path in log_file.parent.glob(f"{log_file.name}*"):
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                for line in file:
                    match = _ROUND_PATTERN.fullmatch(line.rstrip("\r\n"))
                    if match:
                        candidate = (match.group(1), int(match.group(2)))
                        if latest is None or candidate[0] > latest[0]:
                            latest = candidate
        except OSError:
            # 日志只用于旧版本迁移；单个历史日志不可读不应阻止查询。
            continue
    return 0 if latest is None else latest[1]


def _valid_last_round(state: Any) -> int | None:
    if not isinstance(state, dict):
        return None
    last_round = state.get("last_round")
    if isinstance(last_round, bool) or not isinstance(last_round, int):
        return None
    return last_round if last_round >= 0 else None


def next_round_number(
    state_file: Path = MONITOR_STATE_FILE,
    log_file: Path = LOG_FILE,
) -> int:
    """原子记录并返回下一轮编号；调用方须持有全局实例锁。"""
    try:
        with state_file.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except FileNotFoundError:
        last_round = _last_logged_round(log_file)
    except (json.JSONDecodeError, UnicodeError) as error:
        log.warning(f"轮次状态文件损坏，将从日志恢复: {error}")
        last_round = _last_logged_round(log_file)
    else:
        loaded_round = _valid_last_round(state)
        if loaded_round is None:
            log.warning("轮次状态格式无效，将从日志恢复")
            last_round = _last_logged_round(log_file)
        else:
            last_round = loaded_round

    round_number = last_round + 1
    atomic_write_json(
        state_file,
        {"version": STATE_VERSION, "last_round": round_number},
    )
    return round_number
