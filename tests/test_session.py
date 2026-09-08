import json
import unittest
import urllib.parse
from unittest.mock import Mock, patch

import requests

from grade_monitor.session import (
    GRADE_PAGE_SIZE,
    ApiError,
    JwxtSession,
    SsoLoginError,
    SsoVerificationRequired,
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

    def test_sso_handshake_extracts_flow_key(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()
        cookie_payload = {
            "code": 600901,
            "msg": "没找到TGC",
            "data": {"flowKey": "flow.123456", "service": "https://jwxt.bistu.edu.cn/..."},
        }
        client.session.cookies.get.return_value = urllib.parse.quote(json.dumps(cookie_payload))
        response = Mock(status_code=302)
        client.session.get.return_value = response

        flow_key = client._fetch_sso_handshake()
        self.assertEqual(flow_key, "flow.123456")

    def test_sso_handshake_detects_captcha_risk(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()
        cookie_payload = {
            "code": 600902,
            "data": {"captcha": "aliyun", "flowKey": "flow.123456"},
        }
        client.session.cookies.get.return_value = urllib.parse.quote(json.dumps(cookie_payload))
        client.session.get.return_value = Mock(status_code=302)

        with self.assertRaises(SsoVerificationRequired):
            client._fetch_sso_handshake()

    def test_sso_handshake_missing_cookie_raises(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()
        client.session.cookies.get.return_value = None
        client.session.get.return_value = Mock(status_code=200)

        with self.assertRaises(SsoLoginError):
            client._fetch_sso_handshake()

    def test_sso_public_key_success(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "code": 200,
            "data": {"encrypt": {"algorithm": "sm2", "publicKey": "BN6l0...="}},
        }
        client.session.get.return_value = resp

        self.assertEqual(client._fetch_public_key(), "BN6l0...=")

    def test_sso_submit_login_risk_control_raises_verification(self) -> None:
        client = object.__new__(JwxtSession)
        client.username = "2024012616"
        client.password = "secret"
        client.session = Mock()
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "code": 160065,
            "msg": "请先完成滑动验证码",
            "data": {"captcha": "aliyun"},
        }
        client.session.post.return_value = resp

        with (
            patch("grade_monitor.session.encrypt_sm2", return_value="fake-cipher"),
            self.assertRaises(SsoVerificationRequired),
        ):
            client._submit_login("flow.123", "fake-pub")

    def test_sso_submit_login_bad_credentials_raises_login_error(self) -> None:
        client = object.__new__(JwxtSession)
        client.username = "2024012616"
        client.password = "wrong"
        client.session = Mock()
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "code": 170002,
            "msg": "用户名或密码错误",
            "data": None,
        }
        client.session.post.return_value = resp

        with (
            patch("grade_monitor.session.encrypt_sm2", return_value="fake-cipher"),
            self.assertRaises(SsoLoginError),
        ):
            client._submit_login("flow.123", "fake-pub")

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
