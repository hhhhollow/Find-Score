"""Runtime paths and atomic JSON persistence."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def resolve_runtime_dir() -> Path:
    configured = os.environ.get("FIND_SCORE_HOME")
    if configured:
        return Path(configured).expanduser().resolve()

    source_root = Path(__file__).resolve().parent.parent
    if (source_root / "config.json").is_file():
        return source_root
    return Path.cwd().resolve()


BASE_DIR = resolve_runtime_dir()
CONFIG_FILE = BASE_DIR / "config.json"
CACHE_FILE = BASE_DIR / "grades_cache.json"
COOKIES_FILE = BASE_DIR / "cookies.json"
LOG_FILE = BASE_DIR / "grade_monitor.log"
LOCK_FILE = BASE_DIR / ".grade_monitor.lock"


def atomic_write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
