import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grade_monitor import cli


class CliTests(unittest.TestCase):
    def test_no_command_runs_one_check(self) -> None:
        with patch.object(cli, "check_main", return_value=7) as check:
            self.assertEqual(cli.main([]), 7)
        check.assert_called_once_with()

    def test_check_command_runs_one_check(self) -> None:
        with patch.object(cli, "check_main", return_value=0) as check:
            self.assertEqual(cli.main(["check"]), 0)
        check.assert_called_once_with()

    def test_service_commands_are_routed(self) -> None:
        for action in ("start", "stop", "restart", "status", "render"):
            with self.subTest(action=action):
                with patch.object(cli, "service_main", return_value=0) as service:
                    self.assertEqual(cli.main([action]), 0)
                service.assert_called_once_with([action])

    def test_config_output_redacts_credentials(self) -> None:
        config = {
            "jwxt": {"username": "2024012345", "password": "secret-password"},
            "bark": {
                "key": "secret-bark-key",
                "server": "https://api.day.app",
                "group": "Find-Score",
                "sound": "bell",
            },
            "interval_minutes": 20,
        }
        output = io.StringIO()
        with patch.object(cli, "load_config", return_value=config):
            with redirect_stdout(output):
                self.assertEqual(cli.main(["config"]), 0)

        text = output.getvalue()
        self.assertIn("2024****45", text)
        self.assertNotIn("secret-password", text)
        self.assertNotIn("secret-bark-key", text)

    def test_logs_show_requested_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grade_monitor.log"
            path.write_text("one\ntwo\nthree\n", encoding="utf-8")
            output = io.StringIO()
            with patch.object(cli, "LOG_FILE", path):
                with redirect_stdout(output):
                    self.assertEqual(cli.main(["logs", "--lines", "2"]), 0)

        self.assertEqual(output.getvalue(), "two\nthree\n")


if __name__ == "__main__":
    unittest.main()
