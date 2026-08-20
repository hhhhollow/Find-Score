import json
import tempfile
import unittest
from pathlib import Path

from grade_monitor.__main__ import _snapshot, _weighted_average
from grade_monitor.config import ConfigError, load_config


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

    def test_multi_user_config_is_rejected(self) -> None:
        raw = {"users": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
