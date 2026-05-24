"""
学期解析、成绩格式化、加权平均分计算。
"""

import re

_YEAR_NAMES = ["大一", "大二", "大三", "大四", "大五", "大六", "大七"]
_SEM_NAMES = {"1": "第一学期", "2": "第二学期", "3": "小学期"}


def parse_entry_year(student_id: str) -> int:
    """从学号解析入学年份，例如 2024012616 → 2024。"""
    m = re.match(r"^(\d{4})", student_id or "")
    return int(m.group(1)) if m else 2024


def format_term(term_code: str, entry_year: int) -> str:
    """将学期代码转为可读格式：2024-2025-1 + entry_year=2024 → 大一第一学期。"""
    m = re.match(r"(\d{4})-(\d{4})-(\d+)", term_code or "")
    if not m:
        return term_code or ""
    start_year, _, sem = m.groups()
    year_idx = int(start_year) - entry_year + 1
    if 1 <= year_idx <= len(_YEAR_NAMES):
        year_name = _YEAR_NAMES[year_idx - 1]
    else:
        year_name = f"第{year_idx}学年"
    sem_name = _SEM_NAMES.get(sem, f"第{sem}学期")
    return year_name + sem_name


def _to_float(v) -> float | None:
    """安全转浮点数，失败返回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def compute_weighted_avg(grades: list[dict]) -> float | None:
    """按学分加权平均；非数字成绩（如"良好"/"优秀"）不计入。"""
    total_score = 0.0
    total_credit = 0.0
    for g in grades:
        score = _to_float(g.get("score"))
        credit = _to_float(g.get("credit"))
        if score is None or credit is None or credit <= 0:
            continue
        total_score += score * credit
        total_credit += credit
    return total_score / total_credit if total_credit > 0 else None


def format_grade(g: dict, entry_year: int,
                 old_score: str | None = None) -> str:
    """将一条成绩格式化为 Telegram HTML 消息片段。"""
    name = g.get("courseName", "未知课程")
    term = g.get("_termCode", "")
    score = g.get("score", "未出")
    credit = g.get("credit", "")
    usual = g.get("usualScore")
    final = g.get("finalScore")

    lines = [f"<b>{name}</b>"]
    if term:
        lines.append(f"学期：{format_term(term, entry_year)}")
    if usual:
        lines.append(f"平时成绩：{usual}")
    if final:
        lines.append(f"期末成绩：{final}")
    if old_score is not None:
        lines.append(f"总成绩：<s>{old_score}</s> → <b>{score}</b>")
    else:
        lines.append(f"总成绩：{score}")
    if credit:
        lines.append(f"学分：{credit}")
    return "\n".join(lines)
