"""运行时 JSON 文件的通用持久化工具。"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    """原子写入 UTF-8 文本，并设置明确权限。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def atomic_write_json(path: Path, data: Any) -> None:
    """写入临时文件后原子替换目标，避免进程中断损坏状态。"""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def safe_name(name: str) -> str:
    """把用户显示名转换为兼容旧版缓存路径的文件名片段。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "default"
