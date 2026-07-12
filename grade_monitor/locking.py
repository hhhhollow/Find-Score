"""macOS/Linux 进程级单实例锁。"""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

from .constants import BASE_DIR


class InstanceAlreadyRunning(RuntimeError):
    """同一运行时目录已有监控进程。"""


@contextmanager
def instance_lock(path: Path | None = None) -> Iterator[IO[str]]:
    """在进程生命周期内持有非阻塞独占锁。"""
    lock_path = path or BASE_DIR / ".grade_monitor.lock"
    file = open(lock_path, "a+", encoding="utf-8")
    try:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstanceAlreadyRunning(
                f"已有 Find-Score 进程在运行（锁: {lock_path}）",
            ) from error
        yield file
    finally:
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        file.close()
