import unittest

from grade_monitor.changes import detect_changes


def grade(course: str, score: str, term: str = "2024-2025-1") -> dict:
    return {
        "_termCode": term,
        "courseNo": course,
        "courseName": course,
        "score": score,
        "credit": "2",
    }


class DetectChangesTests(unittest.TestCase):
    def test_detects_new_and_updated_without_mutating_input(self) -> None:
        current = {"2024-2025-1|old": "80"}

        changes = detect_changes(
            [grade("old", "85"), grade("new", "90")],
            current,
        )

        self.assertEqual(current, {"2024-2025-1|old": "80"})
        self.assertEqual(changes.scores["2024-2025-1|old"], "85")
        self.assertEqual(changes.scores["2024-2025-1|new"], "90")
        self.assertEqual([item["courseNo"] for item in changes.new_grades], ["new"])
        self.assertEqual(len(changes.updated_grades), 1)
        self.assertEqual(changes.updated_grades[0].old_score, "80")

    def test_no_changes(self) -> None:
        changes = detect_changes(
            [grade("course", "90")],
            {"2024-2025-1|course": "90"},
        )

        self.assertFalse(changes.has_changes)


if __name__ == "__main__":
    unittest.main()
