import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from grade_monitor.cache import load_cache, save_cache
from grade_monitor.polling import poll_once


def grade(score: str = "90") -> dict:
    return {
        "_termCode": "2024-2025-1",
        "courseNo": "MATH101",
        "courseName": "数学",
        "score": score,
        "credit": "2",
        "_hasItemScores": False,
    }


def state(score: str = "80") -> dict:
    return {
        "version": 2,
        "initialized": True,
        "scores": {"2024-2025-1|MATH101": score},
        "outbox": None,
        "failure": {"streak": 0, "first_failure_ts": None, "alert_sent": False},
    }


class FakeClient:
    def __init__(self, grades: list[dict]):
        self.grades = grades

    def fetch_all_grades(self) -> list[dict]:
        return self.grades

    def fetch_grade_details(self, _wid: str) -> dict:
        return {}


class PollingTests(unittest.TestCase):
    @patch("grade_monitor.polling.send_local_notification")
    @patch("grade_monitor.polling.send_telegram", return_value=False)
    def test_notification_failure_queues_outbox_without_committing_snapshot(
        self, _telegram, local,
    ) -> None:
        cache = state("80")
        checkpoint = Mock()

        success = poll_once(
            FakeClient([grade("90")]),
            cache,
            "token",
            "chat",
            2024,
            checkpoint=checkpoint,
        )

        self.assertFalse(success)
        self.assertEqual(cache["scores"]["2024-2025-1|MATH101"], "80")
        self.assertIsNotNone(cache["outbox"])
        checkpoint.assert_called_once()
        local.assert_called_once()

    @patch("grade_monitor.polling.send_local_notification")
    @patch("grade_monitor.polling.send_telegram", return_value=True)
    def test_successful_delivery_commits_snapshot(self, telegram, local) -> None:
        cache = state("80")
        checkpoint = Mock()

        success = poll_once(
            FakeClient([grade("90")]),
            cache,
            "token",
            "chat",
            2024,
            checkpoint=checkpoint,
        )

        self.assertTrue(success)
        self.assertEqual(cache["scores"]["2024-2025-1|MATH101"], "90")
        self.assertIsNone(cache["outbox"])
        self.assertGreaterEqual(telegram.call_count, 2)
        local.assert_called_once()

    @patch("grade_monitor.polling.send_local_notification")
    @patch("grade_monitor.polling.send_telegram", return_value=False)
    def test_failed_cold_start_persists_outbox(self, _telegram, local) -> None:
        cache = state()
        cache["scores"] = {}
        cache["initialized"] = False

        success = poll_once(FakeClient([grade()]), cache, "token", "chat", 2024)

        self.assertFalse(success)
        self.assertFalse(cache["initialized"])
        self.assertIsNotNone(cache["outbox"])
        local.assert_not_called()

    @patch("grade_monitor.polling.send_telegram")
    def test_empty_result_is_rejected(self, telegram) -> None:
        cache = state()
        cache["scores"] = {}
        cache["initialized"] = False

        success = poll_once(FakeClient([]), cache, "token", "chat", 2024)

        self.assertFalse(success)
        self.assertFalse(cache["initialized"])
        self.assertIsNone(cache["outbox"])
        telegram.assert_not_called()

    @patch("grade_monitor.polling.send_local_notification")
    @patch("grade_monitor.polling.send_telegram", side_effect=[True, False])
    def test_partial_delivery_resumes_only_remaining_messages(self, telegram, local) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grades_cache.user.json"
            with patch("grade_monitor.cache.cache_path_for", return_value=path):
                cache = state("80")

                def checkpoint() -> None:
                    save_cache("user", cache)

                first = poll_once(
                    FakeClient([grade("90")]),
                    cache,
                    "token",
                    "chat",
                    2024,
                    checkpoint=checkpoint,
                )

                self.assertFalse(first)
                persisted = load_cache("user", migrate_legacy=False)
                self.assertEqual(
                    persisted["scores"]["2024-2025-1|MATH101"], "80",
                )
                self.assertEqual(len(persisted["outbox"]["messages"]), 1)
                local.assert_called_once()

                cache = persisted
                telegram.reset_mock(side_effect=True)
                telegram.return_value = True
                second = poll_once(
                    FakeClient([grade("90")]),
                    cache,
                    "token",
                    "chat",
                    2024,
                    checkpoint=checkpoint,
                )

                self.assertTrue(second)
                telegram.assert_called_once()
                local.assert_called_once()
                final = load_cache("user", migrate_legacy=False)
                self.assertEqual(
                    final["scores"]["2024-2025-1|MATH101"], "90",
                )
                self.assertIsNone(final["outbox"])

    @patch("grade_monitor.polling.send_telegram")
    def test_unchanged_snapshot_sends_nothing(self, telegram) -> None:
        cache = state("90")

        success = poll_once(FakeClient([grade("90")]), cache, "token", "chat", 2024)

        self.assertTrue(success)
        telegram.assert_not_called()


if __name__ == "__main__":
    unittest.main()
