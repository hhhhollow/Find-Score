import time
import unittest
from unittest.mock import MagicMock, patch

from grade_monitor.__main__ import (
    LockContentionError,
    handle_failure,
    main,
    record_success,
)
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
            "last_error_notified_at": 123456.0,
        }
        record_success()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "HEALTHY")
        self.assertEqual(saved["consecutive_failures"], 0)
        self.assertIsNone(saved["last_error"])
        self.assertEqual(saved["last_error_notified_at"], 0)

    @patch("grade_monitor.__main__.send_recovery")
    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    def test_record_success_sends_recovery_when_previously_alerted(
        self,
        load_status_mock: MagicMock,
        save_status_mock: MagicMock,
        send_recovery_mock: MagicMock,
    ) -> None:
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error": "404 Client Error: for url: https://jwxt.bistu.edu.cn/...",
            "consecutive_failures": 3,
            "last_error_notified_at": 1780000000.0,
        }
        send_recovery_mock.return_value = True
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score", "sound": "bell"}}

        record_success(cfg)

        send_recovery_mock.assert_called_once()
        call_args = send_recovery_mock.call_args
        self.assertEqual(call_args.args[0], "test_key")
        body = call_args.args[1]
        self.assertIn("已恢复异常", body)
        self.assertIn("持续异常次数：3 次", body)
        self.assertEqual(call_args.kwargs.get("title"), "🟢 Find-Score 恢复正常")
        self.assertEqual(call_args.kwargs.get("sound"), "bell")

        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "HEALTHY")
        self.assertEqual(saved["consecutive_failures"], 0)
        self.assertIsNone(saved["last_error"])
        self.assertEqual(saved["last_error_notified_at"], 0)

    @patch("grade_monitor.__main__.send_recovery")
    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    def test_record_success_healthy_does_not_send_recovery(
        self,
        load_status_mock: MagicMock,
        save_status_mock: MagicMock,
        send_recovery_mock: MagicMock,
    ) -> None:
        load_status_mock.return_value = {
            "status": "HEALTHY",
            "last_error": None,
            "consecutive_failures": 0,
            "last_error_notified_at": 0,
        }
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        record_success(cfg)

        send_recovery_mock.assert_not_called()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "HEALTHY")

    @patch("grade_monitor.__main__.send_recovery")
    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    def test_record_success_unhealthy_without_prior_alert_does_not_send_recovery(
        self,
        load_status_mock: MagicMock,
        save_status_mock: MagicMock,
        send_recovery_mock: MagicMock,
    ) -> None:
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error": "Transient error",
            "consecutive_failures": 1,
            "last_error_notified_at": 0,
        }
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        record_success(cfg)

        send_recovery_mock.assert_not_called()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "HEALTHY")

    @patch("grade_monitor.__main__.send_recovery")
    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    def test_record_success_handles_send_recovery_exception(
        self,
        load_status_mock: MagicMock,
        save_status_mock: MagicMock,
        send_recovery_mock: MagicMock,
    ) -> None:
        import requests
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error": "Connection error",
            "consecutive_failures": 1,
            "last_error_notified_at": 1780000000.0,
        }
        send_recovery_mock.side_effect = requests.RequestException("Network failure")
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        # Should not raise exception
        record_success(cfg)

        send_recovery_mock.assert_called_once()
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "HEALTHY")
        self.assertEqual(saved["last_error_notified_at"], 0)

    @patch("grade_monitor.__main__.handle_failure")
    @patch("grade_monitor.__main__.record_success")
    @patch("grade_monitor.__main__.instance_lock")
    @patch("grade_monitor.__main__.load_config")
    @patch("grade_monitor.__main__.configure_logging")
    def test_instance_lock_contention_exits_cleanly_without_alert(
        self,
        configure_logging_mock: MagicMock,
        load_config_mock: MagicMock,
        instance_lock_mock: MagicMock,
        record_success_mock: MagicMock,
        handle_failure_mock: MagicMock,
    ) -> None:
        load_config_mock.return_value = {
            "jwxt": {"username": "user", "password": "pwd"},
            "bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score", "sound": "bell"},
            "interval_minutes": 20,
            "alert_cooldown_hours": 6.0,
        }
        instance_lock_mock.side_effect = LockContentionError("已有 Find-Score 查询正在运行")

        exit_code = main()

        self.assertEqual(exit_code, 0)
        handle_failure_mock.assert_not_called()
        record_success_mock.assert_not_called()

    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    def test_dirty_status_fields_do_not_crash_record_success(
        self,
        load_status_mock: MagicMock,
        save_status_mock: MagicMock,
    ) -> None:
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error": "Some error",
            "consecutive_failures": "oops_not_an_int",
            "last_error_notified_at": "oops_not_a_float",
        }
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}

        # Should not raise ValueError
        record_success(cfg)
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "HEALTHY")
        self.assertEqual(saved["consecutive_failures"], 0)
        self.assertEqual(saved["last_error_notified_at"], 0)

    @patch("grade_monitor.__main__.send_alert")
    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    def test_dirty_status_fields_do_not_crash_handle_failure(
        self,
        load_status_mock: MagicMock,
        save_status_mock: MagicMock,
        send_alert_mock: MagicMock,
    ) -> None:
        load_status_mock.return_value = {
            "status": "HEALTHY",
            "last_error": "Some error",
            "consecutive_failures": "invalid",
            "last_error_notified_at": "invalid",
        }
        cfg = {"bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"}}
        send_alert_mock.return_value = True

        # Should not raise ValueError
        handle_failure(RuntimeError("网络失败"), cfg)
        saved = save_status_mock.call_args[0][0]
        self.assertEqual(saved["status"], "UNHEALTHY")
        self.assertEqual(saved["consecutive_failures"], 1)
        self.assertGreater(saved["last_error_notified_at"], 0)

    @patch("grade_monitor.__main__.save_status")
    @patch("grade_monitor.__main__.load_status")
    @patch("grade_monitor.__main__.send_alert")
    def test_custom_alert_cooldown_hours_is_respected(
        self,
        send_alert_mock: MagicMock,
        load_status_mock: MagicMock,
        save_status_mock: MagicMock,
    ) -> None:
        one_hour_ago = time.time() - 3600
        three_hours_ago = time.time() - (3 * 3600)
        cfg = {
            "bark": {"key": "test_key", "server": "https://api.day.app", "group": "Find-Score"},
            "alert_cooldown_hours": 2.0,
        }

        # 1 hour ago with 2h cooldown: debounced, no alert
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error_notified_at": one_hour_ago,
            "consecutive_failures": 2,
        }
        handle_failure(RuntimeError("持续故障"), cfg)
        send_alert_mock.assert_not_called()

        # 3 hours ago with 2h cooldown: expired, sends alert
        load_status_mock.return_value = {
            "status": "UNHEALTHY",
            "last_error_notified_at": three_hours_ago,
            "consecutive_failures": 3,
        }
        send_alert_mock.return_value = True
        handle_failure(RuntimeError("持续故障"), cfg)
        send_alert_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
