"""成绩快照差异计算；本模块不执行网络或文件 I/O。"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

Grade = dict[str, Any]


def grade_cache_key(grade: Mapping[str, Any]) -> str:
    """生成稳定的成绩缓存键：学期代码 + 最可靠的课程标识。"""
    term = str(grade.get("_termCode") or "").strip()
    course_no = str(grade.get("courseNo") or "").strip()
    wid = str(grade.get("WID") or "").strip()
    course_name = str(grade.get("courseName") or "").strip()

    # 教务接口偶尔可能返回空 KCH。旧实现只在 courseNo 字段不存在时
    # 才回退到课程名，因此多个空 KCH 记录会全部碰撞到 "term|"。
    course = course_no or wid or course_name
    return f"{term}|{course}"


@dataclass(frozen=True, slots=True)
class GradeUpdate:
    grade: Grade
    old_score: str


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """一次快照对比结果，以及成功投递后应提交的新快照。"""

    scores: dict[str, str]
    new_grades: tuple[Grade, ...]
    updated_grades: tuple[GradeUpdate, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.new_grades or self.updated_grades)


def detect_changes(
    grades: Sequence[Grade], current_scores: Mapping[str, str],
) -> ChangeSet:
    """纯函数式比较成绩，不修改传入的缓存。"""
    next_scores = {str(key): str(value) for key, value in current_scores.items()}
    new_grades: list[Grade] = []
    updated_grades: list[GradeUpdate] = []

    for grade in grades:
        key = grade_cache_key(grade)
        new_score = str(grade.get("score", ""))
        old_score = next_scores.get(key)
        next_scores[key] = new_score
        if old_score is None:
            new_grades.append(grade)
        elif old_score != new_score:
            updated_grades.append(GradeUpdate(grade, old_score))

    return ChangeSet(
        scores=next_scores,
        new_grades=tuple(new_grades),
        updated_grades=tuple(updated_grades),
    )
