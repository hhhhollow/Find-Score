import unittest

from grade_monitor.formatting import compute_weighted_avg, format_grade


class FormattingTests(unittest.TestCase):
    def test_escapes_telegram_html_and_keeps_zero_values(self) -> None:
        message = format_grade(
            {
                "courseName": "A < B & C",
                "score": "0<1",
                "usualScore": 0,
                "finalScore": "",
                "credit": 0,
            },
            2024,
        )

        self.assertIn("A &lt; B &amp; C", message)
        self.assertIn("总成绩：0&lt;1", message)
        self.assertIn("平时成绩：0", message)
        self.assertIn("学分：0", message)

    def test_weighted_average_ignores_non_finite_values(self) -> None:
        average = compute_weighted_avg(
            [
                {"score": "90", "credit": "2"},
                {"score": "nan", "credit": "9"},
                {"score": "inf", "credit": "9"},
            ],
        )

        self.assertEqual(average, 90)

    def test_escaped_field_cannot_expand_past_message_limit(self) -> None:
        message = format_grade(
            {"courseName": "&" * 2000, "score": "90", "credit": "2"},
            2024,
        )

        self.assertLess(len(message), 4000)
        self.assertNotIn("&&", message)


if __name__ == "__main__":
    unittest.main()
