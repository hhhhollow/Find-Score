import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from grade_monitor.__main__ import _snapshot, _weighted_average, main, run_once
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

    @patch("grade_monitor.__main__.atomic_write_json")
    @patch("grade_monitor.__main__.load_cache")
    @patch("grade_monitor.__main__._fetch_grades")
    @patch("grade_monitor.__main__.JwxtSession")
    @patch("grade_monitor.__main__.load_config")
    def test_temporarily_missing_grade_stays_in_cache(
        self,
        load_config_mock: MagicMock,
        session_class: MagicMock,
        fetch_grades_mock: MagicMock,
        load_cache_mock: MagicMock,
        write_json_mock: MagicMock,
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
        write_json_mock.assert_called_once_with(
            CACHE_FILE,
            {
                "2025-2026-2|A01": "90",
                "2025-2026-2|B01": "80",
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
