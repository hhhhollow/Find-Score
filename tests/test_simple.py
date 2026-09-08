import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from grade_monitor.__main__ import (
    _failure_notification_due,
    _snapshot,
    _weighted_average,
    main,
    run_once,
)
from grade_monitor.config import ConfigError, load_config
from grade_monitor.storage import CACHE_FILE


class CoreTests(unittest.TestCase):
    def test_snapshot_uses_term_and_course(self) -> None:
        grades = [
            {"_termCode": "2025-2026-2", "courseNo": "A01", "score": "90"},
        ]
        self.assertEqual(_snapshot(grades), {"2025-2026-2|A01": "90"})

    def test_weighted_average(self) -> None:
        grades = [
            {"score": "90", "credit": "2"},
            {"score": "80", "credit": "1"},
        ]
        self.assertAlmostEqual(_weighted_average(grades), 260 / 3)

    def test_single_user_config(self) -> None:
        raw = {
            "jwxt": {"username": "2024012345", "password": "secret"},
            "bark": {"key": "abc"},
            "interval_minutes": 10,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            cfg = load_config(path)
        self.assertEqual(cfg["jwxt"]["username"], "2024012345")
        self.assertEqual(cfg["bark"]["server"], "https://api.day.app")
        self.assertEqual(cfg["interval_minutes"], 10)

    def test_empty_bark_key_after_normalization_is_rejected(self) -> None:
        raw = {
            "jwxt": {"username": "2024012345", "password": "secret"},
            "bark": {"key": "///"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "bark.key"):
                load_config(path)

    def test_multi_user_config_is_rejected(self) -> None:
        raw = {"users": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_malformed_bark_server_is_config_error(self) -> None:
        raw = {
            "jwxt": {"username": "2024012345", "password": "secret"},
            "bark": {"key": "abc", "server": "https://["},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "bark.server"):
                load_config(path)

    @patch("grade_monitor.__main__.FAILURE_NOTIFY_FILE")
    @patch("grade_monitor.__main__.time.time", return_value=10_000.0)
    def test_failure_notification_cooldown(
        self,
        time_mock: MagicMock,
        marker_mock: MagicMock,
    ) -> None:
        marker_mock.stat.return_value.st_mtime = 10_000.0 - 29 * 60
        self.assertFalse(_failure_notification_due())

        marker_mock.stat.return_value.st_mtime = 10_000.0 - 31 * 60
        self.assertTrue(_failure_notification_due())
        time_mock.assert_called()

    @patch("grade_monitor.__main__._record_failure_notification")
    @patch("grade_monitor.__main__._failure_notification_due", return_value=True)
    @patch("grade_monitor.__main__._send", return_value=True)
    @patch("grade_monitor.__main__.COOKIES_FILE")
    @patch("grade_monitor.__main__.JwxtSession")
    @patch("grade_monitor.__main__.load_config")
    def test_login_failure_sends_bark(
        self,
        load_config_mock: MagicMock,
        session_class: MagicMock,
        cookies_file_mock: MagicMock,
        send_mock: MagicMock,
        due_mock: MagicMock,
        record_mock: MagicMock,
    ) -> None:
        load_config_mock.return_value = {
            "jwxt": {"username": "2024012345", "password": "secret"},
            "bark": {
                "key": "abc",
                "server": "https://api.day.app",
                "group": "Find-Score",
                "sound": "bell",
            },
            "interval_minutes": 20,
        }
        cookies_file_mock.exists.return_value = False
        client = session_class.return_value.__enter__.return_value
        client.login.return_value = False

        with self.assertRaisesRegex(RuntimeError, "教务系统登录失败"):
            run_once()

        due_mock.assert_called_once_with()
        send_mock.assert_called_once_with(
            load_config_mock.return_value,
            "教务系统登录失败\n\nFind-Score 后续查询会继续重试。",
            "⚠️ Find-Score 查询失败",
        )
        record_mock.assert_called_once_with()

    @patch("grade_monitor.__main__._send")
    @patch("grade_monitor.__main__.atomic_write_json")
    @patch("grade_monitor.__main__.load_cache")
    @patch("grade_monitor.__main__._fetch_grades")
    @patch("grade_monitor.__main__.JwxtSession")
    @patch("grade_monitor.__main__.load_config")
    def test_cache_is_written_only_after_notified_change(
        self,
        load_config_mock: MagicMock,
        session_class: MagicMock,
        fetch_grades_mock: MagicMock,
        load_cache_mock: MagicMock,
        write_json_mock: MagicMock,
        send_mock: MagicMock,
    ) -> None:
        load_config_mock.return_value = {
            "jwxt": {"username": "2024012345", "password": "secret"},
            "bark": {
                "key": "abc",
                "server": "https://api.day.app",
                "group": "Find-Score",
                "sound": "bell",
            },
            "interval_minutes": 20,
        }
        session_class.return_value.__enter__.return_value.login.return_value = True
        fetch_grades_mock.return_value = [
            {"_termCode": "2025-2026-2", "courseNo": "A01", "score": "90"},
        ]
        load_cache_mock.return_value = {
            "2025-2026-2|A01": "90",
            "2025-2026-2|B01": "80",
        }

        self.assertTrue(run_once())
        write_json_mock.assert_not_called()
        send_mock.assert_not_called()

        fetch_grades_mock.return_value.append(
            {"_termCode": "2025-2026-2", "courseNo": "C01", "score": "95"},
        )
        send_mock.return_value = False
        with self.assertRaisesRegex(RuntimeError, "缓存未更新"):
            run_once()
        write_json_mock.assert_not_called()

        send_mock.return_value = True
        self.assertTrue(run_once())
        self.assertEqual(send_mock.call_count, 2)
        write_json_mock.assert_called_once_with(
            CACHE_FILE,
            {
                "2025-2026-2|A01": "90",
                "2025-2026-2|B01": "80",
                "2025-2026-2|C01": "95",
            },
        )

    def test_logging_setup_failure_returns_error(self) -> None:
        with patch(
            "grade_monitor.__main__.configure_logging",
            side_effect=OSError("log unavailable"),
        ):
            self.assertEqual(main(), 1)


if __name__ == "__main__":
    unittest.main()
