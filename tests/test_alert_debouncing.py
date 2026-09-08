import time
import unittest
from unittest.mock import MagicMock, patch

from grade_monitor.__main__ import handle_failure, record_success
from grade_monitor.session import SsoVerificationRequired


class AlertDebouncingTests(unittest.TestCase):
    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    @patch("grade_monitor.__main__.send_alert")
    def test_first_failure_sends_alert(self, send_alert_mock: MagicMock, load_status_mock: MagicMock, save_status_mock: MagicMock) -> None:
        load_status_mock.return_value = {
            "status": "HEALTHY",
            "last_error_notified_at": 0,
            "consecutive_failures": 0,
        }
        send_alert_mock.return_value = True
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        handle_failure(RuntimeError("网络超时"), cfg)

        send_alert_mock.assert_called_once()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "UNHEALTHY")
        self.assertEqual(saved["consecutive_failures"], 1)
        self.assertGreater(saved["last_error_notified_at"], 0)

    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    @patch("grade_monitor.__main__.send_alert")
    def test_repeated_failure_within_cooldown_is_debounced(self, send_alert_mock: MagicMock, load_status_mock: MagicMock, save_status_mock: MagicMock) -> None:
        recent_time = time.time() - 300  # 5 minutes ago (cooldown is 6 hours)
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error_notified_at": recent_time,
            "consecutive_failures": 1,
        }
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        handle_failure(RuntimeError("网络超时"), cfg)

        send_alert_mock.assert_not_called()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "UNHEALTHY")
        self.assertEqual(saved["consecutive_failures"], 2)

    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    @patch("grade_monitor.__main__.send_alert")
    def test_failure_after_cooldown_sends_alert_again(self, send_alert_mock: MagicMock, load_status_mock: MagicMock, save_status_mock: MagicMock) -> None:
        old_time = time.time() - (7 * 3600)  # 7 hours ago (cooldown is 6 hours)
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error_notified_at": old_time,
            "consecutive_failures": 50,
        }
        send_alert_mock.return_value = True
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        handle_failure(RuntimeError("持续故障"), cfg)

        send_alert_mock.assert_called_once()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["consecutive_failures"], 51)
        self.assertGreater(saved["last_error_notified_at"], old_time)

    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    @patch("grade_monitor.__main__.send_alert")
    def test_sso_verification_required_sends_custom_alert(self, send_alert_mock: MagicMock, load_status_mock: MagicMock, save_status_mock: MagicMock) -> None:
        load_status_mock.return_value = {
            "status": "HEALTHY",
            "last_error_notified_at": 0,
            "consecutive_failures": 0,
        }
        send_alert_mock.return_value = True
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        handle_failure(SsoVerificationRequired("触发阿里滑块"), cfg)

        send_alert_mock.assert_called_once()
        call_args = send_alert_mock.call_args
        title = call_args.kwargs.get("title", "")
        body = call_args.args[1] if len(call_args.args) > 1 else ""
        self.assertIn("风控", title)
        self.assertIn("Cookie", body)

    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    def test_record_success_resets_state(self, load_status_mock: MagicMock, save_status_mock: MagicMock) -> None:
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error": "Some error",
            "consecutive_failures": 5,
        }
        record_success()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "HEALTHY")
        self.assertEqual(saved["consecutive_failures"], 0)
        self.assertIsNone(saved["last_error"])


if __name__ == "__main__":
    unittest.main()
