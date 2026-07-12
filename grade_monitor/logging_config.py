"""应用日志配置；仅在 CLI 入口显式初始化处理器。"""

import logging
import logging.handlers
import sys
from pathlib import Path

from .constants import LOG_FILE

log = logging.getLogger("grade_monitor")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    log.addHandler(logging.NullHandler())


def configure_logging(log_file: Path = LOG_FILE) -> None:
    """幂等安装控制台和滚动文件处理器。"""
    if any(getattr(handler, "_grade_monitor_managed", False) for handler in log.handlers):
        return

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler._grade_monitor_managed = True  # type: ignore[attr-defined]
    log.addHandler(stream_handler)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError as error:
        log.warning(f"无法创建日志文件 {log_file}，仅使用标准输出: {error}")
        return
    file_handler.setFormatter(formatter)
    file_handler._grade_monitor_managed = True  # type: ignore[attr-defined]
    log.addHandler(file_handler)
