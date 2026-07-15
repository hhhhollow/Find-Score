import unittest
from contextlib import contextmanager, nullcontext
from unittest.mock import patch

from grade_monitor.__main__ import _run_round, _sleep_until, main
from grade_monitor.locking import InstanceAlreadyRunning


class CliTests(unittest.TestCase):
    @patch("grade_monitor.__main__.process_user", side_effect=[True, False])
    def test_round_reports_any_user_failure(self, process_user) -> None:
        users = [{"name": "a"}, {"name": "b"}]

        self.assertFalse(_run_round(users, 1))
        self.assertEqual(process_user.call_count, 2)

    @patch("grade_monitor.__main__.configure_logging")
    @patch("grade_monitor.__main__.os.umask")
    @patch("grade_monitor.__main__.instance_lock", return_value=nullcontext())
    @patch("grade_monitor.__main__.run_once", return_value=False)
    def test_once_failure_returns_nonzero(self, _run, _lock, _umask, _logging) -> None:
        self.assertEqual(main(["once"]), 1)

    @patch("grade_monitor.__main__.configure_logging")
    @patch("grade_monitor.__main__.os.umask")
    @patch("grade_monitor.__main__.instance_lock", return_value=nullcontext())
    @patch("grade_monitor.__main__.run_once", return_value=True)
    def test_once_success_returns_zero(self, _run, _lock, _umask, _logging) -> None:
        self.assertEqual(main(["once"]), 0)

    @patch("grade_monitor.__main__._run_round", return_value=True)
    @patch("grade_monitor.__main__.next_round_number", return_value=42)
    @patch("grade_monitor.__main__.load_config", return_value={"users": [{"name": "a"}]})
    def test_once_uses_persistent_round_number(self, _config, _number, run) -> None:
        from grade_monitor.__main__ import run_once

        self.assertTrue(run_once())
        run.assert_called_once_with([{"name": "a"}], 42)

    @patch("grade_monitor.__main__.configure_logging")
    @patch("grade_monitor.__main__.os.umask")
    def test_second_instance_returns_distinct_exit_code(self, _umask, _logging) -> None:
        @contextmanager
        def blocked():
            raise InstanceAlreadyRunning("已运行")
            yield

        with patch("grade_monitor.__main__.instance_lock", return_value=blocked()):
            self.assertEqual(main(["once"]), 2)

    @patch("grade_monitor.__main__.time.sleep")
    @patch("grade_monitor.__main__.time.time", side_effect=[100, 200])
    def test_segmented_wall_clock_wait_catches_up_after_sleep(self, _time, sleep) -> None:
        _sleep_until(150, max_chunk_seconds=30)

        sleep.assert_called_once_with(30)


if __name__ == "__main__":
    unittest.main()
