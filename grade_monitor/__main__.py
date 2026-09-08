"""Run one grade check and exit."""

import fcntl
import json
import logging
import math
import os
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from requests import RequestException

from .config import AppConfig, ConfigError, load_config
from .notify import send_alert, send_bark
from .session import (
    ApiError,
    Grade,
    JwxtSession,
    SessionExpired,
    SsoLoginError,
    SsoVerificationRequired,
)
from .storage import (
    CACHE_FILE,
    COOKIES_FILE,
    LOCK_FILE,
    LOG_FILE,
    atomic_write_json,
    load_status,
    save_status,
)

log = logging.getLogger("find-score")
_YEAR_NAMES = ["大一", "大二", "大三", "大四", "大五", "大六", "大七"]
_SEM_NAMES = {"1": "第一学期", "2": "第二学期", "3": "小学期"}


def configure_logging() -> None:
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    log.addHandler(console)
    log.addHandler(file_handler)


@contextmanager
def instance_lock() -> Iterator[None]:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w", encoding="utf-8") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("已有 Find-Score 查询正在运行") from error
        file.write(str(os.getpid()))
        file.flush()
        yield


def _cache_key(grade: Grade) -> str:
    term = str(grade.get("_termCode") or "").strip()
    course = (
        str(grade.get("courseNo") or "").strip()
        or str(grade.get("WID") or "").strip()
        or str(grade.get("courseName") or "").strip()
    )
    return f"{term}|{course}"


def _snapshot(grades: Sequence[Grade]) -> dict[str, str]:
    return {_cache_key(grade): str(grade.get("score", "")) for grade in grades}


def load_cache() -> dict[str, str] | None:
    try:
        with open(CACHE_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        log.warning("成绩缓存损坏，将重新建立基线")
        return None

    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in data.items()
    ):
        log.warning("成绩缓存格式无效，将重新建立基线")
        return None
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _parse_entry_year(student_id: str) -> int:
    match = re.match(r"^(\d{4})", student_id)
    return int(match.group(1)) if match else 2024


def _format_term(term_code: str, entry_year: int) -> str:
    match = re.match(r"(\d{4})-(\d{4})-(\d+)", term_code or "")
    if not match:
        return term_code or ""
    start_year, _, semester = match.groups()
    year_index = int(start_year) - entry_year + 1
    year_name = (
        _YEAR_NAMES[year_index - 1]
        if 1 <= year_index <= len(_YEAR_NAMES)
        else f"第{year_index}学年"
    )
    return year_name + _SEM_NAMES.get(semester, f"第{semester}学期")


def _number(value: object) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _weighted_average(grades: Sequence[Grade]) -> float | None:
    total = 0.0
    credits = 0.0
    for grade in grades:
        score = _number(grade.get("score"))
        credit = _number(grade.get("credit"))
        if score is None or credit is None or credit <= 0:
            continue
        total += score * credit
        credits += credit
    return total / credits if credits else None


def _enrich_grade(client: JwxtSession, grade: Grade) -> None:
    if not grade.get("_hasItemScores"):
        return
    try:
        details = client.fetch_grade_details(str(grade.get("WID", "")))
    except (RequestException, SessionExpired, ApiError):
        return
    for item in details.get("itemScores", []):
        code = item.get("code")
        if code == "PSCJ":
            grade["usualScore"] = item.get("value", "")
        elif code == "QMCJ":
            grade["finalScore"] = item.get("value", "")


def _format_grade(grade: Grade, entry_year: int, old_score: str | None = None) -> str:
    lines = [str(grade.get("courseName", "未知课程"))]
    term = str(grade.get("_termCode", ""))
    if term:
        lines.append(f"学期：{_format_term(term, entry_year)}")
    usual = grade.get("usualScore")
    if usual not in (None, ""):
        lines.append(f"平时成绩：{usual}")
    final = grade.get("finalScore")
    if final not in (None, ""):
        lines.append(f"期末成绩：{final}")
    score = grade.get("score", "")
    if old_score is None:
        lines.append(f"总成绩：{score}")
    else:
        lines.append(f"总成绩：{old_score} → {score}")
    credit = grade.get("credit")
    if credit not in (None, ""):
        lines.append(f"学分：{credit}")
    return "\n".join(lines)


ALERT_COOLDOWN_SECONDS = 6 * 3600


def record_success() -> None:
    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
    status = load_status()
    status["status"] = "HEALTHY"
    status["last_success_at"] = now_iso
    status["consecutive_failures"] = 0
    status["last_error"] = None
    save_status(status)


def handle_failure(error: Exception, cfg: AppConfig | None = None) -> None:
    now = time.time()
    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
    status = load_status()

    last_status = status.get("status", "HEALTHY")
    last_notified = float(status.get("last_error_notified_at", 0) or 0)
    failures = int(status.get("consecutive_failures", 0) or 0) + 1

    status["status"] = "UNHEALTHY"
    status["last_error"] = str(error)
    status["last_error_at"] = now_iso
    status["consecutive_failures"] = failures

    cooldown = ALERT_COOLDOWN_SECONDS
    if cfg and "alert_cooldown_hours" in cfg:
        try:
            cooldown = float(cfg["alert_cooldown_hours"]) * 3600
        except (TypeError, ValueError):
            pass

    should_notify = (last_status == "HEALTHY") or (now - last_notified >= cooldown)

    if should_notify and cfg is not None:
        if isinstance(error, SsoVerificationRequired):
            title = "⚠️ Find-Score 风控告警"
            text = (
                "教务系统会话已失效且触发风控验证（滑块/验证码/MFA）。\n"
                "自动重登受阻，请在浏览器中登录后执行 'find-score cookie' 导入 Cookie。"
            )
        else:
            title = "⚠️ Find-Score 监控异常"
            text = f"成绩监控运行失败：{error}\n若持续失败请检查网络或执行 'find-score check' 排查。"

        try:
            bark = cfg["bark"]
            sent = send_alert(
                bark["key"],
                text,
                title=title,
                server=bark["server"],
                group=bark["group"],
                sound="alarm",
            )
            if sent:
                status["last_error_notified_at"] = now
                log.info("已发送异常告警通知 (Bark)")
            else:
                log.warning("异常告警通知发送失败 (Bark 接口返回失败)")
        except (KeyError, TypeError, ValueError, RequestException) as notify_err:
            log.warning("发送异常告警通知发生错误: %s", notify_err)

    save_status(status)


def _fetch_grades(client: JwxtSession) -> list[Grade]:
    try:
        return client.fetch_all_grades()
    except SessionExpired:
        if not client.login():
            raise RuntimeError("教务系统登录失败")
        return client.fetch_all_grades()


def _send(cfg: AppConfig, text: str, title: str) -> bool:
    bark = cfg["bark"]
    return send_bark(
        bark["key"],
        text,
        title=title,
        server=bark["server"],
        group=bark["group"],
        sound=bark["sound"],
    )


def run_once(cfg: AppConfig | None = None) -> bool:
    if cfg is None:
        cfg = load_config()
    jwxt = cfg["jwxt"]

    with JwxtSession(jwxt["username"], jwxt["password"], COOKIES_FILE) as client:
        if not COOKIES_FILE.exists() and not client.login():
            raise RuntimeError("教务系统登录失败")

        grades = _fetch_grades(client)
        if not grades:
            raise RuntimeError("成绩接口返回空列表")

        new_snapshot = _snapshot(grades)
        old_snapshot = load_cache()

        if old_snapshot is None:
            if not _send(
                cfg,
                f"成绩监控已初始化，已缓存 {len(grades)} 门课程。",
                "Find-Score",
            ):
                raise RuntimeError("Bark 初始化通知失败")
            atomic_write_json(CACHE_FILE, new_snapshot)
            log.info("初始化完成，共 %d 门课程", len(grades))
            return True

        new_grades: list[Grade] = []
        updated_grades: list[tuple[Grade, str]] = []
        for grade in grades:
            key = _cache_key(grade)
            score = new_snapshot[key]
            if key not in old_snapshot:
                new_grades.append(grade)
            elif old_snapshot[key] != score:
                updated_grades.append((grade, old_snapshot[key]))

        if not new_grades and not updated_grades:
            log.info("没有成绩变化")
            return True

        entry_year = _parse_entry_year(jwxt["username"])
        sections: list[str] = []

        if new_grades:
            for grade in new_grades:
                _enrich_grade(client, grade)
            sections.append(
                f"🎓 新成绩 {len(new_grades)} 门\n\n"
                + "\n\n".join(_format_grade(grade, entry_year) for grade in new_grades)
            )

        if updated_grades:
            for grade, _old_score in updated_grades:
                _enrich_grade(client, grade)
            sections.append(
                f"🔄 成绩变更 {len(updated_grades)} 门\n\n"
                + "\n\n".join(
                    _format_grade(grade, entry_year, old_score)
                    for grade, old_score in updated_grades
                )
            )

        average = _weighted_average(grades)
        if average is not None:
            sections.append(f"📊 总加权平均分：{average:.2f}")

        if not _send(cfg, "\n\n".join(sections), "🎓 成绩提醒"):
            raise RuntimeError("Bark 通知失败；缓存未更新，下次会重试")

        atomic_write_json(CACHE_FILE, old_snapshot | new_snapshot)
        log.info("已发送 %d 条成绩变化", len(new_grades) + len(updated_grades))
        return True


def main() -> int:
    os.umask(0o077)
    cfg: AppConfig | None = None
    try:
        configure_logging()
        cfg = load_config()
    except (ConfigError, OSError) as error:
        log.error("初始化配置失败: %s", error)
        return 1

    try:
        with instance_lock():
            if run_once(cfg):
                record_success()
                return 0
            return 1
    except (ConfigError, OSError, RuntimeError, RequestException, SsoLoginError) as error:
        log.error("%s", error)
        handle_failure(error, cfg)
        return 1
    except Exception as error:
        log.exception("查询失败")
        handle_failure(error, cfg)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
