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
        with (
            patch.object(cli, "load_config", return_value=config),
            redirect_stdout(output),
        ):
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
            with patch.object(cli, "LOG_FILE", path), redirect_stdout(output):
                self.assertEqual(cli.main(["logs", "--lines", "2"]), 0)

        self.assertEqual(output.getvalue(), "two\nthree\n")

    def test_test_notify_success(self) -> None:
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
        with (
            patch.object(cli, "load_config", return_value=config),
            patch.object(cli, "send_bark", return_value=True) as send_mock,
        ):
            self.assertEqual(cli.main(["test-notify"]), 0)
            send_mock.assert_called_once()

    def test_cookie_status_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cookie_path = Path(directory) / "cookies.json"
            cookie_path.write_text(
                '[{"name": "_WEU", "value": "xyz"}, {"name": "GS_SESSIONID", "value": "123"}]',
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch.object(cli, "COOKIES_FILE", cookie_path), redirect_stdout(output):
                self.assertEqual(cli.main(["cookie"]), 0)

            text = output.getvalue()
            self.assertIn("_WEU=✅", text)
            self.assertIn("GS_SESSIONID=✅", text)

    def test_cookie_import_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cookie_path = Path(directory) / "cookies.json"
            output = io.StringIO()
            with (
                patch.object(cli, "COOKIES_FILE", cookie_path),
                patch.object(cli, "_verify_cookies", return_value=True),
                redirect_stdout(output),
            ):
                ret = cli.main(["cookie", "-i", "_WEU=foo; GS_SESSIONID=bar"])
                self.assertEqual(ret, 0)
                self.assertTrue(cookie_path.is_file())

            text = output.getvalue()
            self.assertIn("已成功保存", text)
            self.assertIn("校验成功", text)

    def test_cookie_clear_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cookie_path = Path(directory) / "cookies.json"
            cookie_path.write_text("[]", encoding="utf-8")
            output = io.StringIO()
            with patch.object(cli, "COOKIES_FILE", cookie_path), redirect_stdout(output):
                ret = cli.main(["cookie", "--clear"])
                self.assertEqual(ret, 0)
                self.assertFalse(cookie_path.exists())


if __name__ == "__main__":
    unittest.main()

