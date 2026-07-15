import json
import tempfile
import unittest
from pathlib import Path

from grade_monitor.rounds import next_round_number


class RoundStateTests(unittest.TestCase):
    def test_round_number_persists_across_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / ".grade_monitor_state.json"
            log_file = root / "grade_monitor.log"

            self.assertEqual(next_round_number(state_file, log_file), 1)
            self.assertEqual(next_round_number(state_file, log_file), 2)
            self.assertEqual(
                json.loads(state_file.read_text(encoding="utf-8"))["last_round"],
                2,
            )

    def test_first_state_continues_latest_logged_round(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / ".grade_monitor_state.json"
            log_file = root / "grade_monitor.log"
            log_file.write_text(
                "2026-07-13 11:00:00,000 [INFO] "
                "--- 第 7 轮查询开始 (PID 1, 1 用户) ---\n",
                encoding="utf-8",
            )

            self.assertEqual(next_round_number(state_file, log_file), 8)

    def test_invalid_state_recovers_from_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / ".grade_monitor_state.json"
            log_file = root / "grade_monitor.log"
            state_file.write_text('{"last_round": true}', encoding="utf-8")
            log_file.write_text(
                "2026-07-13 11:00:00,000 [INFO] "
                "--- 第 3 轮查询开始 (PID 1, 1 用户) ---\n",
                encoding="utf-8",
            )

            self.assertEqual(next_round_number(state_file, log_file), 4)

    def test_migration_uses_latest_log_entry_not_historical_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / ".grade_monitor_state.json"
            log_file = root / "grade_monitor.log"
            log_file.write_text(
                "2026-07-12 10:00:00,000 [INFO] "
                "--- 第 703 轮查询开始 (PID 1, 1 用户) ---\n"
                "2026-07-13 11:00:00,000 [INFO] "
                "--- 第 1 轮查询开始 (PID 2, 1 用户) ---\n",
                encoding="utf-8",
            )

            self.assertEqual(next_round_number(state_file, log_file), 2)


if __name__ == "__main__":
    unittest.main()
