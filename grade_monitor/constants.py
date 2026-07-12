"""
路径常量、URL 端点、全局配置。
"""

import os
from collections.abc import Mapping
from pathlib import Path


def resolve_runtime_dir(
    package_root: Path,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """解析配置和状态目录，兼容源码运行与已安装 CLI。"""
    environment = os.environ if environ is None else environ
    configured = environment.get("FIND_SCORE_HOME")
    if configured:
        return Path(configured).expanduser().resolve()

    source_root = package_root.resolve()
    if (source_root / "config.json").is_file():
        return source_root
    return (Path.cwd() if cwd is None else cwd).resolve()

# ── 路径 ──────────────────────────────────────────────────────────────────────
BASE_DIR = resolve_runtime_dir(Path(__file__).parent.parent)
CONFIG_FILE = BASE_DIR / "config.json"
GRADES_CACHE_FILE = BASE_DIR / "grades_cache.json"  # 旧版兼容，迁移用
LOG_FILE = BASE_DIR / "grade_monitor.log"

# ── CAS 认证 ─────────────────────────────────────────────────────────────────
CAS_HOST = "https://wxjw.bistu.edu.cn"
CAS_LOGIN_PATH = "/authserver/login"
CAS_CAPTCHA_CHECK = "/authserver/checkNeedCaptcha.htl"

# ── 教务系统 ─────────────────────────────────────────────────────────────────
JWXT_BASE = "https://jwxt.bistu.edu.cn"
JWXT_SERVICE = f"{JWXT_BASE}/jwapp/sys/emappagelog/modules/emappagelog/loginNew.do"
CJZHCXAPP = f"{JWXT_BASE}/jwapp/sys/cjzhcxapp"
CXWDCJ_URL = f"{CJZHCXAPP}/modules/wdcj/cxwdcj.do"   # 成绩列表
DETAILS_URL = f"{CJZHCXAPP}/api/wdcj/details.do"       # 单门课分项成绩

# ── 请求头 ───────────────────────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── 失败告警阈值 ─────────────────────────────────────────────────────────────
# 连续失败 ≥ ALERT_STREAK 次 且持续 ≥ ALERT_DURATION 秒 才发 Telegram 告警
ALERT_STREAK = 2
ALERT_DURATION = 3600  # 1 小时
