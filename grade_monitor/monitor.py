"""
单用户处理：登录 + 轮询 + 失败告警状态管理。
"""

import time

from .cache import load_cache, safe_name, save_cache
from .constants import ALERT_DURATION, ALERT_STREAK, BASE_DIR
from .formatting import parse_entry_year
from .logging_config import log
from .notify import send_telegram
from .polling import poll_once
from .session import JwxtSession


def process_user(user: dict) -> bool:
    """处理单个用户：登录 + 查询 + 推送 + 更新失败状态。返回 success。"""
    name = user["name"]
    jwxt = user["jwxt"]
    tg = user["telegram"]
    bot_token, chat_id = tg["bot_token"], tg["chat_id"]
    entry_year = parse_entry_year(jwxt["username"])

    log.info(f"[{name}] 开始查询成绩...")
    cache = load_cache(name)
    fail = cache["failure"]

    cookies_path = BASE_DIR / f"cookies.{safe_name(name)}.json"
    client = JwxtSession(jwxt["username"], jwxt["password"], cookies_path)

    success = False
    try:
        # 有 cookies 直接试；没有先登。失效时 poll_once 内部会捕获并自动重登。
        if cookies_path.exists() or client.login():
            success = poll_once(
                client, cache, bot_token, chat_id, entry_year, name,
            )
    except Exception as e:
        log.error(
            f"[{name}] 处理异常: {type(e).__name__}: {e}", exc_info=True,
        )

    # ── 更新失败状态 ──────────────────────────────────────────────────
    if success:
        if fail.get("alert_sent"):
            send_telegram(
                bot_token, chat_id, f"✅ [{name}] 成绩查询恢复正常",
            )
        fail["streak"] = 0
        fail["first_failure_ts"] = None
        fail["alert_sent"] = False
    else:
        fail["streak"] = int(fail.get("streak") or 0) + 1
        if fail.get("first_failure_ts") is None:
            fail["first_failure_ts"] = time.time()
        elapsed = time.time() - fail["first_failure_ts"]
        if (not fail["alert_sent"]
                and fail["streak"] >= ALERT_STREAK
                and elapsed >= ALERT_DURATION):
            send_telegram(
                bot_token, chat_id,
                f"⚠️ [{name}] 成绩查询连续 {fail['streak']} 次失败"
                f"（已持续 {elapsed / 3600:.1f} 小时），请检查日志",
            )
            fail["alert_sent"] = True
        log.warning(
            f"[{name}] 失败 {fail['streak']} 次"
            f"（已 {elapsed / 60:.0f} 分钟）"
        )

    save_cache(name, cache)
    return success
