import unittest
from unittest.mock import patch

from grade_monitor.monitor import _record_result


def failure_state(streak: int = 0, first: float | None = None, alert: bool = False):
    return {"streak": streak, "first_failure_ts": first, "alert_sent": alert}


class FailureStateTests(unittest.TestCase):
    @patch("grade_monitor.monitor.ALERT_STREAK", 2)
    @patch("grade_monitor.monitor.ALERT_DURATION", 60)
    @patch("grade_monitor.monitor.send_telegram", return_value=False)
    def test_failed_alert_delivery_is_retried_later(self, send) -> None:
        failure = failure_state(streak=1, first=0)

        _record_result(failure, False, "token", "chat", "alice", now=120)

        self.assertFalse(failure["alert_sent"])
        send.assert_called_once()

    @patch("grade_monitor.monitor.send_telegram", return_value=False)
    def test_failed_recovery_delivery_keeps_pending_flag(self, send) -> None:
        failure = failure_state(streak=3, first=0, alert=True)

        _record_result(failure, True, "token", "chat", "alice", now=120)

        self.assertEqual(failure["streak"], 0)
        self.assertIsNone(failure["first_failure_ts"])
        self.assertTrue(failure["alert_sent"])
        send.assert_called_once()

    @patch("grade_monitor.monitor.ALERT_STREAK", 1)
    @patch("grade_monitor.monitor.ALERT_DURATION", 0)
    @patch("grade_monitor.monitor.send_telegram", return_value=True)
    def test_new_outage_is_not_suppressed_by_failed_recovery(self, send) -> None:
        failure = failure_state(streak=0, first=None, alert=True)

        _record_result(failure, False, "token", "chat", "a<b&c", now=120)

        self.assertTrue(failure["alert_sent"])
        message = send.call_args.args[2]
        self.assertIn("a&lt;b&amp;c", message)


if __name__ == "__main__":
    unittest.main()
