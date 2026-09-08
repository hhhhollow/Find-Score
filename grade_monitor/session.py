"""BISTU SSO login and grade API access."""

import json
import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .crypto import encrypt_sm2
from .storage import atomic_write_json

SSO_HOST = "https://sso.bistu.edu.cn"
SSO_LOGIN_URL = f"{SSO_HOST}/login"
SSO_RULES_URL = f"{SSO_HOST}/api/reset/rules"
SSO_SUBMIT_URL = f"{SSO_HOST}/username-password/login"

JWXT_BASE = "https://jwxt.bistu.edu.cn"
JWXT_SERVICE = f"{JWXT_BASE}/jwapp/sys/yjsrzfwapp/bistuLogin/casLogin.do"
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


class SsoLoginError(RuntimeError):
    """SSO 登录失败（认证或网络异常）。"""


class SsoVerificationRequired(SsoLoginError):
    """SSO 触发人机验证码、多因子认证或设备确认等风控。"""


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    result = str(value)
    return result if result else default


def _build_session() -> requests.Session:
    """Build a session that automatically retries GET requests only.

    Credential submission is a POST and must never be retried by urllib3.
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

    def __enter__(self) -> "JwxtSession":  # noqa: PYI034 -- Python 3.10 has no typing.Self
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.close()

    def _load_cookies(self) -> None:
        if self.cookies_path is None:
            return
        try:
            with open(self.cookies_path, encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                for cookie in data:
                    if not isinstance(cookie, dict):
                        continue
                    self.session.cookies.set(
                        str(cookie.get("name", "")),
                        str(cookie.get("value", "")),
                        domain=str(cookie.get("domain", "")),
                        path=str(cookie.get("path", "/")),
                    )
            elif isinstance(data, dict):
                for key, val in data.items():
                    self.session.cookies.set(
                        str(key),
                        str(val),
                        domain="jwxt.bistu.edu.cn",
                        path="/",
                    )
            else:
                raise TypeError("cookie 文件必须是数组或字典")
        except (OSError, ValueError, TypeError, KeyError):
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

    def _fetch_sso_handshake(self) -> str:
        """访问 SSO 登录页，提取 flowKey。"""
        try:
            response = self.session.get(
                SSO_LOGIN_URL,
                params={"service": JWXT_SERVICE},
                allow_redirects=False,
                timeout=15,
            )
        except requests.RequestException as err:
            raise SsoLoginError(f"访问 SSO 登录页失败: {err}") from err

        cookie_info_raw = self.session.cookies.get("COOKIE_INFO")
        if not cookie_info_raw and response.status_code in (301, 302, 303, 307):
            loc = response.headers.get("Location")
            if loc:
                try:
                    self.session.get(urllib.parse.urljoin(SSO_HOST, loc), timeout=15)
                    cookie_info_raw = self.session.cookies.get("COOKIE_INFO")
                except requests.RequestException:
                    pass

        if not cookie_info_raw:
            raise SsoLoginError("SSO 握手失败：未获取到 COOKIE_INFO")

        try:
            info = json.loads(urllib.parse.unquote(cookie_info_raw))
        except (ValueError, TypeError) as err:
            raise SsoLoginError("SSO 握手失败：COOKIE_INFO 解析失败") from err

        if not isinstance(info, dict):
            raise SsoLoginError("SSO 握手响应结构异常")

        data = info.get("data")
        if isinstance(data, dict):
            if data.get("captcha") or data.get("mfa"):
                raise SsoVerificationRequired(
                    f"SSO 触发风控验证 ({data.get('captcha') or data.get('mfa')})"
                )
            flow_key = data.get("flowKey")
            if flow_key and isinstance(flow_key, str):
                return flow_key

        raise SsoLoginError(f"SSO 握手未返回有效的 flowKey (code={info.get('code')})")

    def _fetch_public_key(self) -> str:
        """获取国密 SM2 公钥。"""
        try:
            response = self.session.get(SSO_RULES_URL, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as err:
            raise SsoLoginError(f"获取 SSO 规则配置失败: {err}") from err
        except ValueError as err:
            raise SsoLoginError("SSO 规则配置返回了无效 JSON") from err

        if not isinstance(payload, dict) or payload.get("code") != 200:
            code = payload.get("code") if isinstance(payload, dict) else "?"
            raise SsoLoginError(f"获取 SSO 规则配置失败 (code={code})")

        data = payload.get("data")
        encrypt_info = data.get("encrypt") if isinstance(data, dict) else None
        public_key = encrypt_info.get("publicKey") if isinstance(encrypt_info, dict) else None
        if not public_key or not isinstance(public_key, str):
            raise SsoLoginError("SSO 规则配置未返回有效 publicKey")

        return public_key

    def _submit_login(self, flow_key: str, public_key: str) -> str:
        """提交国密 SM2 加密的凭据，返回 Ticket 消费服务链接。"""
        try:
            encrypted_password = encrypt_sm2(self.password, public_key)
        except Exception as err:
            raise SsoLoginError(f"国密 SM2 密码加密失败: {err}") from err

        payload = {
            "flowKey": flow_key,
            "username": self.username,
            "password": encrypted_password,
        }
        try:
            response = self.session.post(
                SSO_SUBMIT_URL,
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            res = response.json()
        except requests.RequestException as err:
            raise SsoLoginError(f"提交 SSO 凭据网络异常: {err}") from err
        except ValueError as err:
            raise SsoLoginError("SSO 登录响应非 JSON") from err

        if not isinstance(res, dict):
            raise SsoLoginError("SSO 登录响应结构异常")

        code = res.get("code")
        msg = str(res.get("msg", ""))
        data = res.get("data")

        if code == 666666 and isinstance(data, dict):
            service_url = data.get("service")
            if not service_url or not isinstance(service_url, str):
                raise SsoLoginError("SSO 登录成功但未返回 service URL")
            return service_url

        if isinstance(data, dict) and (data.get("captcha") or data.get("mfa")):
            raise SsoVerificationRequired(
                f"SSO 触发人机验证码或风控 ({data.get('captcha') or data.get('mfa')}): {msg}"
            )
        if any(kw in msg for kw in ("验证码", "滑动", "人机", "二次验证", "MFA", "常用设备", "设备")):
            raise SsoVerificationRequired(f"SSO 触发人机验证码或风控: {msg}")

        raise SsoLoginError(f"SSO 登录失败 (code={code}): {msg}")

    def _register_app_context(self) -> bool:
        try:
            response = self.session.get(
                f"{CJZHCXAPP}/*default/index.do",
                params={"THEME": "indigo", "forceApp": "cjzhcxapp"},
                timeout=15,
                allow_redirects=False,
            )
        except requests.RequestException:
            return False
        return 200 <= response.status_code < 300

    def login(self) -> bool:
        """执行全新 SSO 登录流程。"""
        self.session.cookies.clear()
        flow_key = self._fetch_sso_handshake()
        public_key = self._fetch_public_key()
        service_target = self._submit_login(flow_key, public_key)

        if not service_target.startswith("http"):
            service_target = urllib.parse.urljoin(SSO_HOST, service_target)

        try:
            response = self.session.get(service_target, allow_redirects=True, timeout=20)
            response.raise_for_status()
        except requests.RequestException as err:
            raise SsoLoginError(f"消费 SSO Ticket 失败: {err}") from err

        if JWXT_BASE not in response.url or not self._register_app_context():
            raise SsoLoginError(f"教务系统会话注册失败 (当前 URL: {response.url})")

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
