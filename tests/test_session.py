import unittest
from unittest.mock import Mock, patch

import requests

from grade_monitor.session import (
    GRADE_PAGE_SIZE,
    ApiError,
    JwxtSession,
    _build_session,
)


def _grade_payload(rows: list[object]) -> dict:
    return {"code": "0", "datas": {"cxwdcj": {"rows": rows}}}


class SessionResponseTests(unittest.TestCase):
    def test_missing_rows_is_api_error(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(return_value={"code": "0", "datas": {}})

        with self.assertRaisesRegex(ApiError, "rows"):
            client.fetch_all_grades()

    def test_empty_rows_is_valid_api_shape(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(return_value=_grade_payload([]))

        self.assertEqual(client.fetch_all_grades(), [])

    def test_non_object_row_is_api_error(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(return_value=_grade_payload(["bad"]))

        with self.assertRaisesRegex(ApiError, "rows"):
            client.fetch_all_grades()

    def test_fetch_all_grades_paginates_past_200(self) -> None:
        first_page = [
            {
                "XNXQDM": "2025-2026-2",
                "KCH": f"C{index:03d}",
                "KCM": f"Course {index}",
                "XSZCJ": "90",
                "WID": f"W{index:03d}",
            }
            for index in range(GRADE_PAGE_SIZE)
        ]
        second_page = [
            {
                "XNXQDM": "2025-2026-2",
                "KCH": "C200",
                "KCM": "Course 200",
                "XSZCJ": "91",
                "WID": "W200",
            }
        ]
        client = object.__new__(JwxtSession)
        client._post_json = Mock(
            side_effect=[_grade_payload(first_page), _grade_payload(second_page)]
        )

        grades = client.fetch_all_grades()

        self.assertEqual(len(grades), GRADE_PAGE_SIZE + 1)
        self.assertEqual(grades[-1]["courseNo"], "C200")
        self.assertEqual(client._post_json.call_count, 2)
        self.assertEqual(
            client._post_json.call_args_list[1].args[1]["pageNumber"],
            "2",
        )

    def test_repeated_page_is_rejected(self) -> None:
        page = [
            {
                "XNXQDM": "2025-2026-2",
                "KCH": f"C{index:03d}",
                "XSZCJ": "90",
                "WID": f"W{index:03d}",
            }
            for index in range(GRADE_PAGE_SIZE)
        ]
        client = object.__new__(JwxtSession)
        client._post_json = Mock(
            side_effect=[_grade_payload(page), _grade_payload(page)]
        )

        with self.assertRaisesRegex(ApiError, "重复页面"):
            client.fetch_all_grades()

    def test_missing_or_invalid_captcha_response_fails_closed(self) -> None:
        client = object.__new__(JwxtSession)
        client.username = "2024012345"
        client.session = Mock()
        response = Mock()
        response.json.return_value = {}
        client.session.get.return_value = response

        with self.assertRaisesRegex(ApiError, "isNeed"):
            client._need_captcha()

    def test_register_app_context_fails_closed(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()
        client.session.get.side_effect = requests.RequestException("network")
        self.assertFalse(client._register_app_context())

        client.session.get.side_effect = None
        client.session.get.return_value = Mock(status_code=500)
        self.assertFalse(client._register_app_context())

        client.session.get.return_value = Mock(status_code=200)
        self.assertTrue(client._register_app_context())
        self.assertFalse(client.session.get.call_args.kwargs["allow_redirects"])

    def test_session_adapter_does_not_retry_post(self) -> None:
        session = _build_session()
        try:
            retry = session.get_adapter("https://").max_retries
            self.assertIn("GET", retry.allowed_methods)
            self.assertNotIn("POST", retry.allowed_methods)
        finally:
            session.close()

    def test_grade_post_retries_transient_status(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()

        transient = Mock(status_code=503, headers={})
        success = Mock(
            status_code=200,
            headers={"Content-Type": "application/json"},
        )
        success.json.return_value = {"code": "0"}
        client.session.post.side_effect = [transient, success]

        with patch("grade_monitor.session.time.sleep") as sleep:
            payload = client._post_json("https://example.invalid/query", {"a": "b"})

        self.assertEqual(payload, {"code": "0"})
        self.assertEqual(client.session.post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_malformed_details_is_api_error(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(
            return_value={
                "code": "0",
                "datas": {"details": {"itemScores": ["bad"]}},
            },
        )

        with self.assertRaisesRegex(ApiError, "itemScores"):
            client.fetch_grade_details("wid")


if __name__ == "__main__":
    unittest.main()
