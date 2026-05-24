"""
教务系统 HTTP 会话管理：CAS 登录、成绩接口。
"""

import json
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cache import atomic_write_json
from .constants import (
    CAS_CAPTCHA_CHECK,
    CAS_HOST,
    CAS_LOGIN_PATH,
    CJZHCXAPP,
    CXWDCJ_URL,
    DETAILS_URL,
    JWXT_BASE,
    JWXT_SERVICE,
    USER_AGENT,
)
from .crypto import encrypt_password
from .logging_config import log

from pathlib import Path


class SessionExpired(Exception):
    """检测到会话失效，需要重新登录。"""


def _build_session() -> requests.Session:
    """创建带重试适配器的 requests.Session（应对学校网络不稳定）。"""
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
    """封装一个用户的教务系统会话（登录 + 成绩查询）。"""

    def __init__(self, username: str, password: str,
                 cookies_path: Path | None = None):
        self.username = username
        self.password = password
        self.cookies_path = cookies_path
        self.session = _build_session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": (
                f"{JWXT_BASE}/jwapp/sys/homeapp/home/index.html"
                f"?contextPath=/jwapp"
            ),
        })
        if cookies_path and cookies_path.exists():
            self._load_cookies()

    # ── Cookies 持久化 ────────────────────────────────────────────────────

    def _load_cookies(self) -> None:
        try:
            with open(self.cookies_path, encoding="utf-8") as f:
                data = json.load(f)
            for c in data:
                self.session.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain", ""),
                    path=c.get("path", "/"),
                )
            log.info(f"[{self.username}] 已加载持久 cookies ({len(data)} 个)")
        except Exception as e:
            log.warning(f"[{self.username}] cookies 加载失败，忽略: {e}")

    def _save_cookies(self) -> None:
        if not self.cookies_path:
            return
        try:
            data = [
                {"name": c.name, "value": c.value,
                 "domain": c.domain, "path": c.path}
                for c in self.session.cookies
            ]
            atomic_write_json(self.cookies_path, data)
        except Exception as e:
            log.warning(f"[{self.username}] cookies 保存失败: {e}")

    def nuke_session(self) -> None:
        """彻底清除所有会话状态：内存 cookies + 持久化文件 + 重建连接池。

        用于 HTTP 403 等 app 上下文失效后的最后手段。
        """
        self.session.cookies.clear()
        if self.cookies_path and self.cookies_path.exists():
            try:
                self.cookies_path.unlink()
                log.info(f"[{self.username}] 已删除持久 cookies 文件")
            except OSError as e:
                log.warning(f"[{self.username}] 删除 cookies 文件失败: {e}")

        old_headers = dict(self.session.headers)
        self.session.close()
        self.session = _build_session()
        self.session.headers.update(old_headers)
        log.info(f"[{self.username}] 会话已彻底重置")

    # ── CAS 登录 ──────────────────────────────────────────────────────────

    def _need_captcha(self) -> bool:
        try:
            r = self.session.get(
                f"{CAS_HOST}{CAS_CAPTCHA_CHECK}",
                params={"username": self.username},
                timeout=10,
            )
            return bool(r.json().get("isNeed", False))
        except Exception:
            return False

    def login(self) -> bool:
        """完整 CAS 登录流程。成功返回 True。"""
        # 清掉旧 cookies，避免残留 CASTGC 让 CAS 跳过登录表单
        self.session.cookies.clear()
        try:
            r = self.session.get(
                f"{CAS_HOST}{CAS_LOGIN_PATH}",
                params={"service": JWXT_SERVICE},
                timeout=15,
            )
            html = r.text

            m = re.search(r'name="execution"\s+value="([^"]+)"', html)
            if not m:
                log.error("未能获取 CAS execution token")
                return False
            execution = m.group(1)

            m = re.search(r'id="pwdEncryptSalt"\s+value="([^"]*)"', html)
            salt = m.group(1) if m else ""

            if self._need_captcha():
                log.error(
                    "触发验证码保护，请用浏览器手动登录一次或等几小时后重试"
                )
                return False

            r = self.session.post(
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

            if JWXT_BASE in r.url:
                log.info(f"[{self.username}] CAS 登录成功")
                # 注册 cjzhcxapp 应用上下文（不然 cxwdcj.do 会 403）
                try:
                    ctx_r = self.session.get(
                        f"{CJZHCXAPP}/*default/index.do",
                        params={"THEME": "indigo", "forceApp": "cjzhcxapp"},
                        timeout=15,
                    )
                    if ctx_r.status_code in (401, 403):
                        log.error(
                            f"[{self.username}] cjzhcxapp 上下文注册失败 "
                            f"(HTTP {ctx_r.status_code})，成绩子系统可能不可用"
                        )
                        return False
                except Exception as e:
                    log.warning(f"访问 cjzhcxapp 入口失败（可能不影响）: {e}")
                self._save_cookies()
                return True

            if "showErrorTip" in r.text or "密码错误" in r.text:
                log.error("登录失败：账号或密码错误")
            else:
                log.error(f"登录失败，当前 URL: {r.url}")
            return False

        except Exception as e:
            log.error(f"登录异常: {type(e).__name__}: {e}")
            return False

    # ── 数据接口 ──────────────────────────────────────────────────────────

    def _post_json(self, url: str, data: dict | None = None) -> dict:
        """POST + 检测会话失效（302 跳 CAS / 401-403 / 非 JSON 响应）。"""
        r = self.session.post(
            url, data=data or {}, timeout=15, allow_redirects=False,
        )
        if r.status_code in (301, 302):
            raise SessionExpired(
                f"被重定向到 {r.headers.get('Location', '?')}"
            )
        if r.status_code in (401, 403):
            raise SessionExpired(
                f"HTTP {r.status_code}（app 上下文/会话失效）"
            )
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise SessionExpired(f"非 JSON 响应 ({ctype})")
        return r.json()

    def fetch_all_grades(self) -> list[dict]:
        """拉所有成绩，字段规范化后返回。"""
        data = self._post_json(
            CXWDCJ_URL, {"pageSize": "200", "pageNumber": "1"},
        )
        if data.get("code") != "0":
            return []
        rows = (
            (data.get("datas") or {}).get("cxwdcj") or {}
        ).get("rows") or []
        return [
            {
                "_termCode": row.get("XNXQDM", ""),
                "courseNo": row.get("KCH", ""),
                "courseName": row.get("KCM", "未知课程"),
                "score": row.get("XSZCJ", ""),
                "credit": row.get("XF", ""),
                "gradePoint": row.get("JD", ""),
                "WID": row.get("WID", ""),
                "_hasItemScores": bool(row.get("FXCJ")),
            }
            for row in rows
        ]

    def fetch_grade_details(self, wid: str) -> dict:
        """拉单门课的分项成绩 → 返回 details 子对象，含 itemScores[]。"""
        if not wid:
            return {}
        data = self._post_json(DETAILS_URL, {"WID": wid})
        if data.get("code") != "0":
            return {}
        return (data.get("datas") or {}).get("details") or {}
