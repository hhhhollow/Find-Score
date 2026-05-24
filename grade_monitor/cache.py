"""
成绩缓存 & 原子文件写入。

缓存结构（每用户一个 JSON 文件）：
{
  "scores": { "term|courseNo": "分数" },
  "failure": {"streak": int, "first_failure_ts": float|None, "alert_sent": bool}
}
"""

import json
import os
import re
import tempfile
from pathlib import Path

from .constants import BASE_DIR, GRADES_CACHE_FILE
from .logging_config import log


# ── 原子写文件 ────────────────────────────────────────────────────────────────

def atomic_write_json(path: Path, data) -> None:
    """先写临时文件再 rename，避免中途崩溃损坏缓存。"""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── 文件名安全化 ──────────────────────────────────────────────────────────────

def safe_name(name: str) -> str:
    """把用户标识转成可作文件名的安全字符串。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "default"


# ── 缓存路径 & 读写 ──────────────────────────────────────────────────────────

def cache_path_for(user_name: str) -> Path:
    return BASE_DIR / f"grades_cache.{safe_name(user_name)}.json"


def _empty_state() -> dict:
    return {
        "scores": {},
        "failure": {"streak": 0, "first_failure_ts": None, "alert_sent": False},
    }


def load_cache(user_name: str) -> dict:
    """加载用户缓存；自动迁移旧的 grades_cache.json。"""
    path = cache_path_for(user_name)

    # 旧缓存迁移（仅对第一个用户）
    if not path.exists() and GRADES_CACHE_FILE.exists():
        try:
            os.replace(GRADES_CACHE_FILE, path)
            log.info(f"已把旧缓存 grades_cache.json 迁移为 {path.name}")
        except OSError as e:
            log.warning(f"迁移旧缓存失败: {e}")

    if not path.exists():
        return _empty_state()

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f"{path.name} 损坏，重建: {e}")
        return _empty_state()

    # 确保字段完整
    data.setdefault("scores", {})
    fail = data.setdefault("failure", {})
    fail.setdefault("streak", 0)
    fail.setdefault("first_failure_ts", None)
    fail.setdefault("alert_sent", False)
    data.pop("active_terms", None)  # 旧字段，清理
    return data


def save_cache(user_name: str, cache: dict) -> None:
    atomic_write_json(cache_path_for(user_name), cache)


def grade_cache_key(g: dict) -> str:
    """从成绩字典生成缓存键：term|courseNo。"""
    return f"{g.get('_termCode', '')}|{g.get('courseNo', g.get('courseName', ''))}"
