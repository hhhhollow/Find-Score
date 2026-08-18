"""运行时 JSON 文件的通用持久化工具。"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def _sync_file(file) -> None:
    """把 Python/stdio 缓冲刷新到底层文件，降低异常掉电造成状态丢失的风险。"""
    file.flush()
    os.fsync(file.fileno())


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    """原子写入 UTF-8 文本，并设置明确权限。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            _sync_file(file)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def atomic_write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    """以私有权限原子写 JSON，避免中断损坏或状态文件被其他用户读取。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            _sync_file(file)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def safe_name(name: str) -> str:
    """把用户显示名转换为兼容旧版缓存路径的文件名片段。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "default"
