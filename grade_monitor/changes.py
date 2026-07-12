"""成绩快照差异计算；本模块不执行网络或文件 I/O。"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

Grade = dict[str, Any]


def grade_cache_key(grade: Mapping[str, Any]) -> str:
    """生成稳定的成绩缓存键：学期代码|课程号。"""
    term = grade.get("_termCode", "")
    course = grade.get("courseNo", grade.get("courseName", ""))
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
