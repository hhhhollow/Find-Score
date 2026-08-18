"""单次轮询编排：拉取、比较、可靠通知与快照提交。"""

from collections.abc import Callable
from html import escape
from typing import Any

from .changes import ChangeSet, GradeUpdate, detect_changes
from .formatting import (
    compute_weighted_avg,
    escape_message_text,
    format_grade,
    format_term,
)
from .logging_config import log
from .notify import (
    build_messages,
    send_bark,
    send_local_notification,
    send_telegram,
)
from .session import JwxtSession, SessionExpired

Checkpoint = Callable[[], None]


def _configured_channel_names(channels: dict) -> list[str]:
    names: list[str] = []
    telegram_cfg = channels.get("telegram")
    if (
        isinstance(telegram_cfg, dict)
        and telegram_cfg.get("bot_token")
        and telegram_cfg.get("chat_id")
    ):
        names.append("telegram")
    bark_cfg = channels.get("bark")
    if isinstance(bark_cfg, dict) and bark_cfg.get("key"):
        names.append("bark")
    return names


def _send_channel(channels: dict, channel: str, text: str, user_name: str) -> bool:
    """只向指定渠道发送一次，供 outbox 做逐渠道确认。"""
    if channel == "telegram":
        cfg = channels.get("telegram")
        if not isinstance(cfg, dict):
            return False
        return send_telegram(cfg["bot_token"], cfg["chat_id"], text)

    if channel == "bark":
        cfg = channels.get("bark")
        if not isinstance(cfg, dict):
            return False
        return send_bark(
            key=cfg["key"],
            text=text,
            title=f"🎓 [{user_name}] 成绩提醒",
            server=cfg.get("server", "https://api.day.app"),
            group=cfg.get("group", "Find-Score"),
            sound=cfg.get("sound", "bell"),
        )

    log.error(f"未知通知渠道: {channel}")
    return False


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
    """把一次变更渲染成可逐条确认的远端通知 outbox。"""
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
    channels: dict,
    checkpoint: Checkpoint | None,
) -> None:
    """发送前持久化消息、目标快照以及本轮必须确认的通知渠道。"""
    if not messages:
        raise ValueError("不能创建空通知 outbox")
    required_channels = _configured_channel_names(channels)
    if not required_channels:
        raise ValueError("没有有效的远端通知渠道")
    cache["outbox"] = {
        "messages": list(messages),
        "target_scores": dict(target_scores),
        "target_initialized": target_initialized,
        "required_channels": required_channels,
        "delivered_channels": [],
    }
    _checkpoint(checkpoint)


def _flush_outbox(
    cache: dict,
    channels_or_token: Any,
    chat_id_or_user: str = "",
    user_name_or_checkpoint: Any = None,
    checkpoint: Checkpoint | None = None,
) -> bool:
    """逐消息、逐渠道确认 outbox；只重试尚未确认的渠道。"""
    if isinstance(channels_or_token, dict):
        channels = channels_or_token
        user_name = chat_id_or_user or "default"
        real_checkpoint = user_name_or_checkpoint if callable(user_name_or_checkpoint) else checkpoint
    else:
        channels = {
            "telegram": {
                "bot_token": channels_or_token,
                "chat_id": chat_id_or_user,
            }
        }
        user_name = "default"
        real_checkpoint = user_name_or_checkpoint if callable(user_name_or_checkpoint) else checkpoint

    outbox = cache.get("outbox")
    if not isinstance(outbox, dict):
        return True

    messages = outbox.get("messages")
    target_scores = outbox.get("target_scores")
    target_initialized = outbox.get("target_initialized")
    required_channels = outbox.get("required_channels")
    delivered_channels = outbox.get("delivered_channels", [])
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(target_scores, dict)
        or not isinstance(target_initialized, bool)
        or (
            required_channels is not None
            and not (
                isinstance(required_channels, list)
                and required_channels
                and all(isinstance(channel, str) and channel for channel in required_channels)
            )
        )
        or not isinstance(delivered_channels, list)
        or not all(isinstance(channel, str) and channel for channel in delivered_channels)
    ):
        log.error("通知 outbox 状态无效")
        return False

    active_channels = _configured_channel_names(channels)
    if required_channels is None:
        if not active_channels:
            log.error("通知 outbox 无可用远端渠道")
            return False
        required_channels = list(active_channels)
        delivered_channels = []
        outbox["required_channels"] = required_channels
        outbox["delivered_channels"] = delivered_channels
        _checkpoint(real_checkpoint)
    else:
        # 配置变更时，已移除的旧渠道不应永久阻塞历史 outbox；新加渠道则只接收
        # 之后新建的 outbox，避免收到配置前的旧消息。
        retained = [
            channel for channel in required_channels
            if channel in active_channels
        ]
        if not retained and active_channels:
            retained = list(active_channels)
            delivered_channels = []
        else:
            delivered_channels = [
                channel for channel in delivered_channels
                if channel in retained
            ]
        if retained != required_channels or delivered_channels != outbox.get("delivered_channels"):
            required_channels = retained
            outbox["required_channels"] = required_channels
            outbox["delivered_channels"] = delivered_channels
            _checkpoint(real_checkpoint)

    if not required_channels:
        log.error("通知 outbox 所需渠道均不可用")
        return False

    while messages:
        failed = False
        for channel in required_channels:
            if channel in delivered_channels:
                continue
            if _send_channel(channels, channel, messages[0], user_name):
                delivered_channels.append(channel)
                outbox["delivered_channels"] = delivered_channels
                _checkpoint(real_checkpoint)
            else:
                failed = True

        if failed:
            return False

        if len(messages) > 1:
            del messages[0]
            delivered_channels = []
            outbox["delivered_channels"] = delivered_channels
        else:
            cache["scores"] = dict(target_scores)
            cache["initialized"] = target_initialized
            cache["outbox"] = None
        _checkpoint(real_checkpoint)
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
    bot_token: str = "",
    chat_id: str = "",
    entry_year: int = 0,
    user_name: str = "default",
    *,
    channels: dict | None = None,
    checkpoint: Checkpoint | None = None,
) -> bool:
    """执行一次轮询；所有目标渠道确认后才提交其对应的成绩快照。"""
    if channels is not None:
        active_channels = channels
    else:
        active_channels = {
            "telegram": {
                "bot_token": bot_token,
                "chat_id": chat_id,
            }
        }

    tag = f"[{user_name}]"
    who = f"<b>[{escape(user_name, quote=False)}]</b>"

    if cache.get("outbox") is not None:
        log.info(f"{tag} 继续投递上轮未完成的通知")
        if not _flush_outbox(cache, active_channels, user_name, checkpoint):
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
        try:
            _queue_outbox(
                cache,
                messages,
                changes.scores,
                True,
                active_channels,
                checkpoint,
            )
        except ValueError as error:
            log.error(f"{tag} 无法创建初始化通知: {error}")
            return False
        if not _flush_outbox(cache, active_channels, user_name, checkpoint):
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
        _queue_outbox(
            cache,
            messages,
            changes.scores,
            True,
            active_channels,
            checkpoint,
        )
    except ValueError as error:
        log.error(f"{tag} 通知内容无法入队: {error}")
        return False
    # 本地通知是 best-effort，不必等待远端 outbox 全部确认。
    _send_local_change_messages(changes, user_name)
    if not _flush_outbox(cache, active_channels, user_name, checkpoint):
        log.error(f"{tag} 通知未全部送达，已保留 outbox 供下轮续传")
        return False
    return True
