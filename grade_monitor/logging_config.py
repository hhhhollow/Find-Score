"""
日志配置：控制台 + 带滚动的文件日志。

其他模块统一使用：
    from .logging_config import log
"""

import logging
import logging.handlers
import sys

from .constants import LOG_FILE

log = logging.getLogger("grade_monitor")
log.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

# 控制台
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_sh)

# 文件（2 MB 滚动，保留 3 个备份）
_fh = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8",
)
_fh.setFormatter(_fmt)
log.addHandler(_fh)
