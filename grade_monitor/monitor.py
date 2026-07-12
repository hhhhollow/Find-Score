"""单用户监控编排与失败告警状态管理。"""

import time
from html import escape

from .cache import load_cache, save_cache
from .constants import ALERT_DURATION, ALERT_STREAK, BASE_DIR
from .formatting import parse_entry_year
from .logging_config import log
from .notify import send_telegram
from .polling import poll_once
from .session import JwxtSession
from .storage import safe_name


def _record_result(
    failure: dict,
    success: bool,
    bot_token: str,
    chat_id: str,
    user_name: str,
    now: float | None = None,
) -> None:
    """更新失败状态；告警状态只在相应 Telegram 消息送达后翻转。"""
    current_time = time.time() if now is None else now
    escaped_name = escape(user_name, quote=False)

    if success:
        recovery_delivered = True
        if failure.get("alert_sent"):
            recovery_delivered = send_telegram(
                bot_token,
                chat_id,
                f"✅ [{escaped_name}] 成绩监控恢复正常",
            )
        failure["streak"] = 0
        failure["first_failure_ts"] = None
        if recovery_delivered:
            failure["alert_sent"] = False
        return

    # 恢复通知未送达后若立即再次故障，新故障周期不应被旧标志压住。
    if (
        failure.get("alert_sent")
        and not failure.get("streak")
        and failure.get("first_failure_ts") is None
    ):
        failure["alert_sent"] = False

    failure["streak"] = int(failure.get("streak") or 0) + 1
    if failure.get("first_failure_ts") is None:
        failure["first_failure_ts"] = current_time
    elapsed = max(0.0, current_time - float(failure["first_failure_ts"]))

    if (
        not failure.get("alert_sent")
        and failure["streak"] >= ALERT_STREAK
        and elapsed >= ALERT_DURATION
    ):
        delivered = send_telegram(
            bot_token,
            chat_id,
            f"⚠️ [{escaped_name}] 成绩监控连续 {failure['streak']} 次失败"
            f"（已持续 {elapsed / 3600:.1f} 小时），请检查日志",
        )
        if delivered:
            failure["alert_sent"] = True

    log.warning(
        f"[{user_name}] 失败 {failure['streak']} 次"
        f"（已 {elapsed / 60:.0f} 分钟）",
    )


def process_user(user: dict, migrate_legacy: bool = True) -> bool:
    """处理单个用户，持久化边界统一由本层提供。"""
    name = user["name"]
    jwxt = user["jwxt"]
    telegram = user["telegram"]
    bot_token = telegram["bot_token"]
    chat_id = telegram["chat_id"]
    entry_year = parse_entry_year(jwxt["username"])

    log.info(f"[{name}] 开始查询成绩...")
    cache = load_cache(name, migrate_legacy=migrate_legacy)
    failure = cache["failure"]
    cookies_path = BASE_DIR / f"cookies.{safe_name(name)}.json"

    client: JwxtSession | None = None
    success = False

    def checkpoint() -> None:
        save_cache(name, cache)

    try:
        client = JwxtSession(jwxt["username"], jwxt["password"], cookies_path)
        # 持久 cookies 存在时先直接查询；poll_once 会处理失效和重登。
        if cookies_path.exists() or client.login():
            success = poll_once(
                client,
                cache,
                bot_token,
                chat_id,
                entry_year,
                name,
                checkpoint=checkpoint,
            )
    except Exception as error:
        log.error(
            f"[{name}] 处理异常: {type(error).__name__}: {error}",
            exc_info=True,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                log.warning(f"[{name}] 关闭 HTTP 会话失败: {error}")

    _record_result(failure, success, bot_token, chat_id, name)
    save_cache(name, cache)
    return success
