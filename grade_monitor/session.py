"""BISTU CAS login and grade API access."""

import json
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

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
GRADE_PAGE_SIZE = 200
MAX_GRADE_PAGES = 100
_TRANSIENT_STATUSES = {502, 503, 504}


class Grade(TypedDict, total=False):
    _termCode: str
    courseNo: str
    courseName: str
    score: str
    credit: str
    WID: str
    _hasItemScores: bool
    usualScore: str
    finalScore: str


class GradeItemScore(TypedDict, total=False):
    code: str
    value: str


class GradeDetails(TypedDict, total=False):
    itemScores: list[GradeItemScore]


class SessionExpired(Exception):
    """The current login session is no longer valid."""


class ApiError(RuntimeError):
    """The grade system returned an invalid business response."""


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    result = str(value)
    return result if result else default


def _build_session() -> requests.Session:
    """Build a session that automatically retries GET requests only.

    CAS credential submission is a POST and must never be retried by urllib3.
    Read-only grade POST requests have their own explicit retry loop below.
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=sorted(_TRANSIENT_STATUSES),
        allowed_methods=frozenset({"GET"}),
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

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.close()

    def _load_cookies(self) -> None:
        if self.cookies_path is None:
            return
        try:
            with open(self.cookies_path, encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, list):
                raise ValueError("cookie 文件必须是数组")
            for cookie in data:
                if not isinstance(cookie, dict):
                    raise ValueError("cookie 项必须是对象")
                self.session.cookies.set(
                    str(cookie["name"]),
                    str(cookie["value"]),
                    domain=str(cookie.get("domain", "")),
                    path=str(cookie.get("path", "/")),
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
        """Return captcha requirement, failing closed if the check is unreliable."""
        response = self.session.get(
            f"{CAS_HOST}{CAS_CAPTCHA_CHECK}",
            params={"username": self.username},
            timeout=10,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise ApiError("验证码检测返回了无效 JSON") from error
        if not isinstance(payload, Mapping) or "isNeed" not in payload:
            raise ApiError("验证码检测响应缺少 isNeed")

        value = payload["isNeed"]
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1"}:
                return True
            if normalized in {"false", "0"}:
                return False
        raise ApiError("验证码检测响应中的 isNeed 类型无效")

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
        """Log in once. The credential POST is deliberately never auto-retried."""
        self.session.cookies.clear()
        try:
            response = self.session.get(
                f"{CAS_HOST}{CAS_LOGIN_PATH}",
                params={"service": JWXT_SERVICE},
                timeout=15,
            )
            response.raise_for_status()
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
            response.raise_for_status()
        except (requests.RequestException, ApiError, ValueError):
            return False

        if JWXT_BASE not in response.url or not self._register_app_context():
            return False

        self._save_cookies()
        return True

    def _post_json(
        self,
        url: str,
        data: dict[str, str] | None = None,
        retries: int = 3,
    ) -> dict[str, object]:
        """POST an idempotent grade query with explicit bounded retries."""
        attempts = max(1, retries)
        for attempt in range(attempts):
            try:
                response = self.session.post(
                    url,
                    data=data or {},
                    timeout=15,
                    allow_redirects=False,
                )
            except requests.RequestException:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(2**attempt)
                continue

            if response.status_code in _TRANSIENT_STATUSES and attempt + 1 < attempts:
                time.sleep(2**attempt)
                continue
            if response.status_code in (301, 302, 401, 403):
                raise SessionExpired(f"HTTP {response.status_code}")

            response.raise_for_status()
            if "json" not in response.headers.get("Content-Type", "").lower():
                raise SessionExpired("non-JSON response")
            try:
                payload = response.json()
            except ValueError as error:
                raise ApiError("API 返回了无效 JSON") from error
            if not isinstance(payload, dict):
                raise ApiError("API 响应顶层必须是对象")
            return payload

        raise ApiError("成绩查询重试耗尽")

    def _fetch_grade_page(self, page_number: int) -> list[Mapping[str, object]]:
        data = self._post_json(
            CXWDCJ_URL,
            {
                "pageSize": str(GRADE_PAGE_SIZE),
                "pageNumber": str(page_number),
            },
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
        return [row for row in rows if isinstance(row, Mapping)]

    def fetch_all_grades(self) -> list[Grade]:
        """Fetch all pages instead of silently truncating at the first 200 rows."""
        raw_rows: list[Mapping[str, object]] = []
        seen_pages: set[tuple[tuple[str, str, str, str], ...]] = set()

        for page_number in range(1, MAX_GRADE_PAGES + 1):
            rows = self._fetch_grade_page(page_number)
            signature = tuple(
                (
                    _text(row.get("WID")),
                    _text(row.get("KCH")),
                    _text(row.get("XNXQDM")),
                    _text(row.get("XSZCJ")),
                )
                for row in rows
            )
            if rows and signature in seen_pages:
                raise ApiError("成绩列表分页返回了重复页面")
            seen_pages.add(signature)
            raw_rows.extend(rows)

            if len(rows) < GRADE_PAGE_SIZE:
                break
        else:
            raise ApiError(f"成绩列表超过 {MAX_GRADE_PAGES} 页，已停止查询")

        grades: list[Grade] = []
        for row in raw_rows:
            grades.append(
                {
                    "_termCode": _text(row.get("XNXQDM")),
                    "courseNo": _text(row.get("KCH")),
                    "courseName": _text(row.get("KCM"), "未知课程"),
                    "score": _text(row.get("XSZCJ")),
                    "credit": _text(row.get("XF")),
                    "WID": _text(row.get("WID")),
                    "_hasItemScores": bool(row.get("FXCJ")),
                }
            )
        return grades

    def fetch_grade_details(self, wid: str) -> GradeDetails:
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
        if item_scores is None:
            return {}
        if not isinstance(item_scores, list) or not all(
            isinstance(item, Mapping) for item in item_scores
        ):
            raise ApiError("成绩详情接口 itemScores 必须是对象数组")
        return {
            "itemScores": [
                {
                    "code": _text(item.get("code")),
                    "value": _text(item.get("value")),
                }
                for item in item_scores
                if isinstance(item, Mapping)
            ]
        }
