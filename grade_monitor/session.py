"""BISTU CAS login and grade API access."""

import json
import re
from collections.abc import Mapping
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .crypto import encrypt_password
from .storage import atomic_write_json

CAS_HOST = "https://wxjw.bistu.edu.cn"
CAS_LOGIN_PATH = "/authserver/login"
CAS_CAPTCHA_CHECK = "/authserver/checkNeedCaptcha.htl"
JWXT_BASE = "https://jwxt.bistu.edu.cn"
JWXT_SERVICE = f"{JWXT_BASE}/jwapp/sys/emappagelog/modules/emappagelog/loginNew.do"
CJZHCXAPP = f"{JWXT_BASE}/jwapp/sys/cjzhcxapp"
CXWDCJ_URL = f"{CJZHCXAPP}/modules/wdcj/cxwdcj.do"
DETAILS_URL = f"{CJZHCXAPP}/api/wdcj/details.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class SessionExpired(Exception):
    """The current login session is no longer valid."""


class ApiError(RuntimeError):
    """The grade system returned an invalid business response."""


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class JwxtSession:
    def __init__(
        self,
        username: str,
        password: str,
        cookies_path: Path | None = None,
    ):
        self.username = username
        self.password = password
        self.cookies_path = cookies_path
        self.session = _build_session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": f"{JWXT_BASE}/jwapp/sys/homeapp/home/index.html?contextPath=/jwapp",
            }
        )
        if cookies_path and cookies_path.exists():
            self._load_cookies()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "JwxtSession":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def _load_cookies(self) -> None:
        try:
            with open(self.cookies_path, encoding="utf-8") as file:
                data = json.load(file)
            for cookie in data:
                self.session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain", ""),
                    path=cookie.get("path", "/"),
                )
        except Exception:
            self.session.cookies.clear()

    def _save_cookies(self) -> None:
        if not self.cookies_path:
            return
        data = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            }
            for cookie in self.session.cookies
        ]
        atomic_write_json(self.cookies_path, data)

    def _need_captcha(self) -> bool:
        try:
            response = self.session.get(
                f"{CAS_HOST}{CAS_CAPTCHA_CHECK}",
                params={"username": self.username},
                timeout=10,
            )
            return bool(response.json().get("isNeed", False))
        except Exception:
            return False

    @staticmethod
    def _parse_login_form(html: str) -> tuple[str | None, str]:
        execution = re.search(r'name="execution"\s+value="([^"]+)"', html)
        if not execution:
            return None, ""
        salt = re.search(r'id="pwdEncryptSalt"\s+value="([^"]*)"', html)
        return execution.group(1), salt.group(1) if salt else ""

    def _register_app_context(self) -> bool:
        try:
            response = self.session.get(
                f"{CJZHCXAPP}/*default/index.do",
                params={"THEME": "indigo", "forceApp": "cjzhcxapp"},
                timeout=15,
            )
        except requests.RequestException:
            return True
        return response.status_code not in (401, 403)

    def login(self) -> bool:
        self.session.cookies.clear()
        try:
            response = self.session.get(
                f"{CAS_HOST}{CAS_LOGIN_PATH}",
                params={"service": JWXT_SERVICE},
                timeout=15,
            )
            execution, salt = self._parse_login_form(response.text)
            if not execution or self._need_captcha():
                return False

            response = self.session.post(
                f"{CAS_HOST}{CAS_LOGIN_PATH}",
                params={"service": JWXT_SERVICE},
                data={
                    "username": self.username,
                    "password": encrypt_password(self.password, salt),
                    "captcha": "",
                    "_eventId": "submit",
                    "cllt": "userNameLogin",
                    "dllt": "generalLogin",
                    "lt": "",
                    "execution": execution,
                },
                timeout=20,
                allow_redirects=True,
            )
        except requests.RequestException:
            return False

        if JWXT_BASE not in response.url or not self._register_app_context():
            return False

        self._save_cookies()
        return True

    def _post_json(self, url: str, data: dict | None = None) -> dict:
        response = self.session.post(
            url,
            data=data or {},
            timeout=15,
            allow_redirects=False,
        )
        if response.status_code in (301, 302, 401, 403):
            raise SessionExpired(f"HTTP {response.status_code}")
        response.raise_for_status()
        if "json" not in response.headers.get("Content-Type", "").lower():
            raise SessionExpired("non-JSON response")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ApiError("API 响应顶层必须是对象")
        return payload

    def fetch_all_grades(self) -> list[dict]:
        data = self._post_json(
            CXWDCJ_URL,
            {"pageSize": "200", "pageNumber": "1"},
        )
        if data.get("code") != "0":
            raise ApiError(f"成绩列表接口失败 (code={data.get('code', '?')})")
        datas = data.get("datas")
        result = datas.get("cxwdcj") if isinstance(datas, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            raise ApiError("成绩列表接口响应缺少 datas.cxwdcj.rows")

        rows = result["rows"]
        if not all(isinstance(row, Mapping) for row in rows):
            raise ApiError("成绩列表接口 rows 必须全部是对象")

        return [
            {
                "_termCode": row.get("XNXQDM", ""),
                "courseNo": row.get("KCH", ""),
                "courseName": row.get("KCM", "未知课程"),
                "score": row.get("XSZCJ", ""),
                "credit": row.get("XF", ""),
                "WID": row.get("WID", ""),
                "_hasItemScores": bool(row.get("FXCJ")),
            }
            for row in rows
        ]

    def fetch_grade_details(self, wid: str) -> dict:
        if not wid:
            return {}
        data = self._post_json(DETAILS_URL, {"WID": wid})
        if data.get("code") != "0":
            raise ApiError(f"成绩详情接口失败 (code={data.get('code', '?')})")
        datas = data.get("datas")
        details = datas.get("details") if isinstance(datas, dict) else None
        if not isinstance(details, dict):
            raise ApiError("成绩详情接口响应缺少 datas.details")
        item_scores = details.get("itemScores")
        if item_scores is not None and (
            not isinstance(item_scores, list)
            or not all(isinstance(item, Mapping) for item in item_scores)
        ):
            raise ApiError("成绩详情接口 itemScores 必须是对象数组")
        return details
