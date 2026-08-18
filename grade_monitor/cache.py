"""成绩缓存读取、校验与迁移。

缓存结构（每用户一个 JSON 文件）：
{
  "version": 3,
  "initialized": bool,
  "scores": {"term|courseNo": "分数"},
  "outbox": null | {
      "messages": [...],
      "target_scores": {...},
      "target_initialized": bool,
      "required_channels": null | [...],
      "delivered_channels": [...]
  },
  "failure": {"streak": int, "first_failure_ts": float|None, "alert_sent": bool}
}
"""

import json
import math
import os
from pathlib import Path

from .changes import grade_cache_key
from .constants import BASE_DIR, GRADES_CACHE_FILE
from .logging_config import log
from .storage import atomic_write_json, safe_name

CACHE_VERSION = 3


def cache_path_for(user_name: str) -> Path:
    return BASE_DIR / f"grades_cache.{safe_name(user_name)}.json"


def _empty_state() -> dict:
    return {
        "version": CACHE_VERSION,
        "initialized": False,
        "scores": {},
        "outbox": None,
        "failure": {"streak": 0, "first_failure_ts": None, "alert_sent": False},
    }


def _string_list(value: object, *, allow_empty: bool = True) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not allow_empty and not value:
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    # 保序去重，避免损坏状态导致同一渠道被重复发送。
    return list(dict.fromkeys(value))


def _normalize_state(data: object, path: Path) -> dict:
    """把旧缓存或类型不完整的缓存规范化为当前 schema。"""
    if not isinstance(data, dict):
        log.warning(f"{path.name} 顶层不是对象，重建")
        return _empty_state()

    raw_scores = data.get("scores", {})
    if not isinstance(raw_scores, dict):
        log.warning(f"{path.name} 的 scores 类型无效，已清空")
        raw_scores = {}
    scores = {
        str(key): "" if value is None else str(value)
        for key, value in raw_scores.items()
    }

    raw_failure = data.get("failure", {})
    if not isinstance(raw_failure, dict):
        log.warning(f"{path.name} 的 failure 类型无效，已重置")
        raw_failure = {}

    try:
        streak = max(0, int(raw_failure.get("streak", 0)))
    except (TypeError, ValueError):
        streak = 0

    first_failure_ts = raw_failure.get("first_failure_ts")
    if isinstance(first_failure_ts, bool) or not isinstance(
        first_failure_ts, (int, float, type(None)),
    ):
        first_failure_ts = None
    elif first_failure_ts is not None and not math.isfinite(first_failure_ts):
        first_failure_ts = None

    alert_sent = raw_failure.get("alert_sent", False)
    if not isinstance(alert_sent, bool):
        alert_sent = False

    initialized = data.get("initialized")
    if not isinstance(initialized, bool):
        # 旧缓存没有该字段；已有成绩即代表完成过初始化。
        initialized = bool(scores)

    raw_outbox = data.get("outbox")
    outbox = None
    if isinstance(raw_outbox, dict):
        messages = raw_outbox.get("messages")
        target_scores = raw_outbox.get("target_scores")
        target_initialized = raw_outbox.get("target_initialized")
        required_channels_raw = raw_outbox.get("required_channels")
        delivered_channels_raw = raw_outbox.get("delivered_channels", [])

        required_channels = None
        if required_channels_raw is not None:
            required_channels = _string_list(
                required_channels_raw,
                allow_empty=False,
            )
        delivered_channels = _string_list(delivered_channels_raw)

        if (
            isinstance(messages, list)
            and messages
            and all(isinstance(message, str) and message for message in messages)
            and isinstance(target_scores, dict)
            and isinstance(target_initialized, bool)
            and (
                required_channels_raw is None
                or required_channels is not None
            )
            and delivered_channels is not None
        ):
            if required_channels is not None:
                delivered_channels = [
                    channel
                    for channel in delivered_channels
                    if channel in required_channels
                ]
            outbox = {
                "messages": list(messages),
                "target_scores": {
                    str(key): "" if value is None else str(value)
                    for key, value in target_scores.items()
                },
                "target_initialized": target_initialized,
                # 旧版 outbox 没有渠道字段；首次 flush 时再绑定当前配置。
                "required_channels": required_channels,
                "delivered_channels": delivered_channels,
            }
        else:
            log.warning(f"{path.name} 的 outbox 类型无效，将重新生成通知")

    return {
        "version": CACHE_VERSION,
        "initialized": initialized,
        "scores": scores,
        "outbox": outbox,
        "failure": {
            "streak": streak,
            "first_failure_ts": first_failure_ts,
            "alert_sent": alert_sent,
        },
    }


def load_cache(user_name: str, migrate_legacy: bool = True) -> dict:
    """加载用户缓存；自动迁移旧的 grades_cache.json。"""
    path = cache_path_for(user_name)

    if migrate_legacy and not path.exists() and GRADES_CACHE_FILE.exists():
        try:
            os.replace(GRADES_CACHE_FILE, path)
            log.info(f"已把旧缓存 grades_cache.json 迁移为 {path.name}")
        except OSError as error:
            log.warning(f"迁移旧缓存失败: {error}")

    if not path.exists():
        return _empty_state()

    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except Exception as error:
        log.warning(f"{path.name} 损坏，重建: {error}")
        return _empty_state()

    return _normalize_state(data, path)


def save_cache(user_name: str, cache: dict) -> None:
    atomic_write_json(cache_path_for(user_name), cache)


__all__ = [
    "atomic_write_json",
    "cache_path_for",
    "grade_cache_key",
    "load_cache",
    "safe_name",
    "save_cache",
]
