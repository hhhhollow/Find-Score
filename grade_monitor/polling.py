"""
单次轮询逻辑：查询成绩 → 对比缓存 → 推送变更。
"""

from .cache import grade_cache_key, save_cache
from .formatting import (
    compute_weighted_avg,
    format_grade,
    format_term,
)
from .logging_config import log
from .notify import send_batch, send_telegram
from .session import JwxtSession, SessionExpired


def _enrich_with_item_scores(client: JwxtSession, g: dict) -> None:
    """请求 details.do，把平时/期末成绩塞回 g。失败静默。"""
    if not g.get("_hasItemScores"):
        return
    try:
        d = client.fetch_grade_details(g.get("WID", ""))
    except Exception as e:
        log.warning(
            f"取分项成绩失败 ({g.get('courseName')}): "
            f"{type(e).__name__}: {e}"
        )
        return
    for item in d.get("itemScores") or []:
        code = item.get("code", "")
        val = item.get("value", "")
        if code == "PSCJ":
            g["usualScore"] = val
        elif code == "QMCJ":
            g["finalScore"] = val


def poll_once(client: JwxtSession, cache: dict, bot_token: str,
              chat_id: str, entry_year: int,
              user_name: str = "default") -> bool:
    """执行一次成绩查询 + 推送。返回 True 表示成功。"""
    tag = f"[{user_name}]"
    who = f"<b>[{user_name}]</b>"  # Telegram HTML 前缀

    # ── 拉成绩（含会话过期自动重登）────────────────────────────────────
    try:
        grades = client.fetch_all_grades()
    except SessionExpired:
        log.info(f"{tag} 会话已过期，重新登录...")
        if not client.login():
            return False
        try:
            grades = client.fetch_all_grades()
        except SessionExpired as e2:
            # 重新登录后仍 403 → 彻底清除 cookies 后再试一次
            log.warning(
                f"{tag} 重新登录后仍失败: {e2}，彻底清除 cookies 后重试..."
            )
            client.nuke_session()
            if not client.login():
                log.error(f"{tag} 彻底清除后登录仍失败")
                return False
            try:
                grades = client.fetch_all_grades()
            except Exception as e3:
                log.error(f"{tag} 彻底清除后仍失败: {e3}")
                return False
        except Exception as e:
            log.error(f"{tag} 重新登录后仍失败: {e}")
            return False
    except Exception as e:
        log.error(f"{tag} 成绩查询异常: {type(e).__name__}: {e}")
        return False

    if not grades:
        log.warning(f"{tag} 查询返回空，本次跳过")
        return False

    # ── 对比缓存 ──────────────────────────────────────────────────────
    scores_cache: dict = cache["scores"]
    is_cold_start = len(scores_cache) == 0
    new_grades: list[dict] = []
    updated: list[tuple[dict, str]] = []  # (新成绩, 旧分数)

    for g in grades:
        key = grade_cache_key(g)
        new_score = str(g.get("score", ""))
        old = scores_cache.get(key)
        if old is None:
            scores_cache[key] = new_score
            if not is_cold_start:
                new_grades.append(g)
        elif old != new_score:
            scores_cache[key] = new_score
            updated.append((g, old))

    # 冷启动：静默缓存，仅发一条初始化通知
    if is_cold_start:
        save_cache(user_name, cache)
        log.info(f"{tag} 首次运行，已缓存 {len(grades)} 条成绩（不逐条通知）")
        send_telegram(
            bot_token, chat_id,
            f"🎉 {who} 成绩监控已初始化，已缓存 {len(grades)} 门课程，"
            f"后续有新成绩将自动通知！"
        )
        return True

    # ── 补充分项成绩 ──────────────────────────────────────────────────
    for g in (*new_grades, *(u[0] for u in updated)):
        _enrich_with_item_scores(client, g)

    if new_grades or updated:
        save_cache(user_name, cache)

    # ── 推送 ──────────────────────────────────────────────────────────
    if new_grades:
        log.info(f"{tag} 发现 {len(new_grades)} 条新成绩")
        send_batch(
            bot_token, chat_id,
            f"🎓 {who} 发现 {len(new_grades)} 条新成绩！\n{'─' * 20}\n",
            [format_grade(g, entry_year) for g in new_grades],
        )

    if updated:
        log.info(f"{tag} 发现 {len(updated)} 条成绩变更")
        send_batch(
            bot_token, chat_id,
            f"🔄 {who} {len(updated)} 条成绩有变更！\n{'─' * 20}\n",
            [format_grade(g, entry_year, old) for g, old in updated],
        )

    # ── 平均分统计 ────────────────────────────────────────────────────
    if new_grades or updated:
        changed_terms = {g["_termCode"] for g in new_grades}
        changed_terms.update(g["_termCode"] for g, _ in updated)

        avg_lines = [f"📊 {who} 平均分统计"]
        for term in sorted(changed_terms, reverse=True):
            term_grades = [g for g in grades if g.get("_termCode") == term]
            avg = compute_weighted_avg(term_grades)
            if avg is not None:
                avg_lines.append(
                    f"{format_term(term, entry_year)}：{avg:.2f}"
                )
        overall = compute_weighted_avg(grades)
        if overall is not None:
            avg_lines.append(f"总平均分：{overall:.2f}")
        if len(avg_lines) > 1:
            send_telegram(bot_token, chat_id, "\n".join(avg_lines))

    if not new_grades and not updated:
        log.info(f"{tag} 暂无新成绩（共 {len(grades)} 条已知成绩）")

    return True
