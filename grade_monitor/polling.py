"""单次轮询编排：拉取、比较、可靠通知与快照提交。"""

from collections.abc import Callable
from html import escape

from .changes import ChangeSet, GradeUpdate, detect_changes
from .formatting import (
    compute_weighted_avg,
    escape_message_text,
    format_grade,
    format_term,
)
from .logging_config import log
from .notify import build_messages, send_local_notification, send_telegram
from .session import JwxtSession, SessionExpired

Checkpoint = Callable[[], None]


def _enrich_with_item_scores(client: JwxtSession, grade: dict) -> None:
    """请求 details.do，把平时/期末成绩补充到成绩对象。"""
    if not grade.get("_hasItemScores"):
        return
    try:
        details = client.fetch_grade_details(grade.get("WID", ""))
    except Exception as error:
        log.warning(
            f"取分项成绩失败 ({grade.get('courseName')}): "
            f"{type(error).__name__}: {error}",
        )
        return
    for item in details.get("itemScores") or []:
        code = item.get("code", "")
        value = item.get("value", "")
        if code == "PSCJ":
            grade["usualScore"] = value
        elif code == "QMCJ":
            grade["finalScore"] = value


def _fetch_grades_resilient(client: JwxtSession, tag: str) -> list[dict] | None:
    """拉取成绩；会话过期时最多执行两级恢复。"""
    try:
        return client.fetch_all_grades()
    except SessionExpired:
        log.info(f"{tag} 会话已过期，重新登录...")
    except Exception as error:
        log.error(f"{tag} 成绩查询异常: {type(error).__name__}: {error}")
        return None

    if not client.login():
        return None
    try:
        return client.fetch_all_grades()
    except SessionExpired as error:
        log.warning(f"{tag} 重新登录后仍失败: {error}，彻底清除 cookies 后重试...")
    except Exception as error:
        log.error(f"{tag} 重新登录后仍失败: {error}")
        return None

    client.nuke_session()
    if not client.login():
        log.error(f"{tag} 彻底清除后登录仍失败")
        return None
    try:
        return client.fetch_all_grades()
    except Exception as error:
        log.error(f"{tag} 彻底清除后仍失败: {error}")
        return None


def _average_lines(
    grades: list[dict],
    changes: ChangeSet,
    entry_year: int,
) -> list[str]:
    changed_terms = {grade.get("_termCode", "") for grade in changes.new_grades}
    changed_terms.update(
        update.grade.get("_termCode", "") for update in changes.updated_grades
    )

    by_term: dict[str, list[dict]] = {}
    for grade in grades:
        by_term.setdefault(grade.get("_termCode", ""), []).append(grade)

    lines: list[str] = []
    for term in sorted(changed_terms, reverse=True):
        average = compute_weighted_avg(by_term.get(term, []))
        if average is not None:
            term_name = escape_message_text(format_term(term, entry_year))
            lines.append(f"{term_name}：{average:.2f}")
    overall = compute_weighted_avg(grades)
    if overall is not None:
        lines.append(f"总平均分：{overall:.2f}")
    return lines


def _build_change_messages(
    changes: ChangeSet,
    grades: list[dict],
    entry_year: int,
    user_name: str,
) -> list[str]:
    """把一次变更渲染成可逐条确认的 Telegram outbox。"""
    tag = f"[{user_name}]"
    who = f"<b>[{escape(user_name, quote=False)}]</b>"
    messages: list[str] = []

    if changes.new_grades:
        log.info(f"{tag} 发现 {len(changes.new_grades)} 条新成绩")
        messages.extend(
            build_messages(
                f"🎓 {who} 发现 {len(changes.new_grades)} 条新成绩！\n"
                f"{'─' * 20}\n",
                [format_grade(grade, entry_year) for grade in changes.new_grades],
            ),
        )

    if changes.updated_grades:
        log.info(f"{tag} 发现 {len(changes.updated_grades)} 条成绩变更")
        messages.extend(
            build_messages(
                f"🔄 {who} {len(changes.updated_grades)} 条成绩有变更！\n"
                f"{'─' * 20}\n",
                [
                    format_grade(update.grade, entry_year, update.old_score)
                    for update in changes.updated_grades
                ],
            ),
        )

    average_lines = _average_lines(grades, changes, entry_year)
    if average_lines:
        messages.extend(
            build_messages(f"📊 {who} 平均分统计\n", average_lines),
        )
    return messages


def _checkpoint(callback: Checkpoint | None) -> None:
    if callback is not None:
        callback()


def _queue_outbox(
    cache: dict,
    messages: list[str],
    target_scores: dict[str, str],
    target_initialized: bool,
    checkpoint: Checkpoint | None,
) -> None:
    """在发送前持久化待投递消息和其对应的目标快照。"""
    if not messages:
        raise ValueError("不能创建空通知 outbox")
    cache["outbox"] = {
        "messages": list(messages),
        "target_scores": dict(target_scores),
        "target_initialized": target_initialized,
    }
    _checkpoint(checkpoint)


def _flush_outbox(
    cache: dict,
    bot_token: str,
    chat_id: str,
    checkpoint: Checkpoint | None,
) -> bool:
    """逐条发送并确认 outbox；失败时保留尚未确认的消息。"""
    outbox = cache.get("outbox")
    if not isinstance(outbox, dict):
        return True

    messages = outbox.get("messages")
    target_scores = outbox.get("target_scores")
    target_initialized = outbox.get("target_initialized")
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(target_scores, dict)
        or not isinstance(target_initialized, bool)
    ):
        log.error("通知 outbox 状态无效")
        return False

    while messages:
        if not send_telegram(bot_token, chat_id, messages[0]):
            return False
        if len(messages) > 1:
            del messages[0]
        else:
            cache["scores"] = dict(target_scores)
            cache["initialized"] = target_initialized
            cache["outbox"] = None
        _checkpoint(checkpoint)
        if cache.get("outbox") is None:
            break

    return True


def _send_local_change_messages(changes: ChangeSet, user_name: str) -> None:
    if changes.new_grades:
        names = "、".join(
            grade.get("courseName", "?") for grade in changes.new_grades[:3]
        )
        if len(changes.new_grades) > 3:
            names += f" 等{len(changes.new_grades)}门"
        send_local_notification("🎓 新成绩", names, subtitle=user_name)

    if changes.updated_grades:
        updates: tuple[GradeUpdate, ...] = changes.updated_grades
        descriptions = "、".join(
            f"{update.grade.get('courseName', '?')} "
            f"{update.old_score}→{update.grade.get('score', '?')}"
            for update in updates[:3]
        )
        if len(updates) > 3:
            descriptions += f" 等{len(updates)}门"
        send_local_notification("🔄 成绩变更", descriptions, subtitle=user_name)


def poll_once(
    client: JwxtSession,
    cache: dict,
    bot_token: str,
    chat_id: str,
    entry_year: int,
    user_name: str = "default",
    *,
    checkpoint: Checkpoint | None = None,
) -> bool:
    """执行一次轮询；可靠消息确认后才提交其对应的成绩快照。"""
    tag = f"[{user_name}]"
    who = f"<b>[{escape(user_name, quote=False)}]</b>"

    if cache.get("outbox") is not None:
        log.info(f"{tag} 继续投递上轮未完成的通知")
        if not _flush_outbox(cache, bot_token, chat_id, checkpoint):
            log.error(f"{tag} 通知仍未全部送达，下轮继续重试")
            return False

    grades = _fetch_grades_resilient(client, tag)
    if grades is None:
        return False
    if not grades:
        log.warning(f"{tag} 查询返回空，本次不更新状态")
        return False

    scores = cache.get("scores")
    if not isinstance(scores, dict):
        log.error(f"{tag} 缓存 scores 类型无效")
        return False

    initialized = bool(cache.get("initialized", bool(scores)))
    changes = detect_changes(grades, scores)

    if not initialized:
        messages = [
            f"🎉 {who} 成绩监控已初始化，已缓存 {len(grades)} 门课程，"
            f"后续有新成绩将自动通知！",
        ]
        _queue_outbox(cache, messages, changes.scores, True, checkpoint)
        if not _flush_outbox(cache, bot_token, chat_id, checkpoint):
            log.error(f"{tag} 初始化通知失败，已进入 outbox 等待下轮重试")
            return False
        log.info(f"{tag} 首次运行，已缓存 {len(grades)} 条成绩（不逐条通知）")
        send_local_notification(
            "成绩监控",
            f"已缓存 {len(grades)} 门课程，后续新成绩将自动通知",
            subtitle=user_name,
        )
        return True

    for grade in changes.new_grades:
        _enrich_with_item_scores(client, grade)
    for update in changes.updated_grades:
        _enrich_with_item_scores(client, update.grade)

    if not changes.has_changes:
        log.info(f"{tag} 暂无新成绩（共 {len(grades)} 条已知成绩）")
        return True

    try:
        messages = _build_change_messages(changes, grades, entry_year, user_name)
    except ValueError as error:
        log.error(f"{tag} 通知内容无法分批: {error}")
        return False
    _queue_outbox(cache, messages, changes.scores, True, checkpoint)
    # 本地通知是 best-effort，不必等待 Telegram outbox 全部确认。
    _send_local_change_messages(changes, user_name)
    if not _flush_outbox(cache, bot_token, chat_id, checkpoint):
        log.error(f"{tag} 通知未全部送达，已保留 outbox 供下轮续传")
        return False
    return True
