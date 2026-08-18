import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from grade_monitor.notify import (
    build_local_notification_command,
    build_messages,
    send_batch,
    send_local_notification,
    send_telegram,
)


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class NotifyTests(unittest.TestCase):
    def test_builds_macos_notification_without_shell(self) -> None:
        command = build_local_notification_command(
            "Darwin", 'A "title"', "message", "student", "Glass",
        )

        self.assertEqual(command[:2], ["osascript", "-e"])
        self.assertIn('A \\"title\\"', command[2])

    def test_builds_linux_notify_send_arguments(self) -> None:
        command = build_local_notification_command(
            "Linux", "New grade", "Calculus", "alice",
        )

        self.assertEqual(
            command,
            [
                "notify-send",
                "--app-name",
                "Find Score",
                "--",
                "New grade — alice",
                "Calculus",
            ],
        )

    def test_linux_notification_content_cannot_be_parsed_as_options(self) -> None:
        command = build_local_notification_command(
            "Linux", "-urgent", "--help", "",
        )

        self.assertEqual(command[-3:], ["--", "-urgent", "--help"])

    @patch("grade_monitor.notify.subprocess.run")
    @patch("grade_monitor.notify.shutil.which", return_value=None)
    def test_linux_without_notify_send_is_safe_noop(self, _which, run) -> None:
        self.assertFalse(send_local_notification("title", "body", system="Linux"))
        run.assert_not_called()

    @patch(
        "grade_monitor.notify.subprocess.run",
        return_value=CompletedProcess([], 0),
    )
    @patch("grade_monitor.notify.shutil.which", return_value="/usr/bin/notify-send")
    def test_linux_desktop_notification(self, _which, run) -> None:
        self.assertTrue(send_local_notification("title", "body", system="Linux"))
        self.assertEqual(run.call_args.args[0][0], "notify-send")

    def test_build_messages_keeps_html_blocks_intact(self) -> None:
        bodies = [f"<b>{'a' * 2500}</b>", f"<s>{'b' * 2500}</s>"]

        messages = build_messages("header\n", bodies, limit=3000)

        self.assertEqual(len(messages), 2)
        self.assertIn("<b>", messages[0])
        self.assertIn("</b>", messages[0])
        self.assertNotIn("<s>", messages[0])
        self.assertTrue(messages[1].startswith("<s>"))
        self.assertTrue(messages[1].endswith("</s>"))

    @patch("grade_monitor.notify.send_telegram", side_effect=[True, False])
    def test_batch_returns_false_on_partial_failure(self, mocked_send) -> None:
        delivered = send_batch(
            "token", "chat", "", ["<b>" + "a" * 2500 + "</b>", "b" * 2500],
        )

        self.assertFalse(delivered)
        self.assertEqual(mocked_send.call_count, 2)

    @patch("grade_monitor.notify.requests.post", return_value=_Response(400))
    def test_permanent_client_error_is_not_retried(self, mocked_post) -> None:
        delivered = send_telegram("secret-token", "chat", "hello")

        self.assertFalse(delivered)
        mocked_post.assert_called_once()

    @patch(
        "grade_monitor.notify.requests.post",
        return_value=_Response(200, {"ok": True}),
    )
    def test_success_requires_telegram_confirmation(self, mocked_post) -> None:
        self.assertTrue(send_telegram("token", "chat", "hello", retries=1))
        mocked_post.assert_called_once()

    @patch(
        "grade_monitor.notify.requests.post",
        return_value=_Response(200, {"code": 200, "message": "success"}),
    )
    def test_bark_success_requires_code_200(self, mocked_post) -> None:
        from grade_monitor.notify import send_bark
        self.assertTrue(send_bark("testkey", "hello", retries=1))
        mocked_post.assert_called_once()

    @patch(
        "grade_monitor.notify.requests.post",
        return_value=_Response(200, {"code": 500, "message": "failed"}),
    )
    def test_bark_failure_on_non_200_code(self, mocked_post) -> None:
        from grade_monitor.notify import send_bark
        self.assertFalse(send_bark("testkey", "hello", retries=1))
        mocked_post.assert_called_once()

    @patch("grade_monitor.notify.send_bark", return_value=True)
    @patch("grade_monitor.notify.send_telegram", return_value=False)
    def test_notification_channels_success_if_any_channel_succeeds(
        self, mocked_telegram, mocked_bark,
    ) -> None:
        from grade_monitor.notify import send_notification_channels
        channels = {
            "telegram": {"bot_token": "token", "chat_id": "123"},
            "bark": {"key": "testkey"},
        }
        self.assertTrue(send_notification_channels(channels, "<b>msg</b>"))
        mocked_telegram.assert_called_once()
        mocked_bark.assert_called_once()


if __name__ == "__main__":
    unittest.main()
