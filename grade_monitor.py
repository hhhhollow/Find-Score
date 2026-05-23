"""
北京信息科技大学 教务系统成绩监控脚本
CAS 统一认证 → scores API → Telegram 推送
"""

import base64
import json
import logging
import logging.handlers
import os
import re
import secrets
import string
import sys
import tempfile
import time
from pathlib import Path

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# ── 路径常量 ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / "config.json"
GRADES_CACHE_FILE = BASE_DIR / "grades_cache.json"
LOG_FILE = BASE_DIR / "grade_monitor.log"

CAS_HOST = "https://wxjw.bistu.edu.cn"
CAS_LOGIN_PATH = "/authserver/login"
CAS_CAPTCHA_CHECK = "/authserver/checkNeedCaptcha.htl"
JWXT_BASE = "https://jwxt.bistu.edu.cn"
JWXT_SERVICE = f"{JWXT_BASE}/jwapp/sys/emappagelog/modules/emappagelog/loginNew.do"
CJZHCXAPP = f"{JWXT_BASE}/jwapp/sys/cjzhcxapp"
CXWDCJ_URL = f"{CJZHCXAPP}/modules/wdcj/cxwdcj.do"        # 成绩列表
DETAILS_URL = f"{CJZHCXAPP}/api/wdcj/details.do"          # 单门课分项成绩

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── 日志（带滚动）────────────────────────────────────────────────────────────
log = logging.getLogger("grade_monitor")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_sh)
_fh = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_fh.setFormatter(_fmt)
log.addHandler(_fh)


# ── 配置 ─────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


# ── 原子写文件 ───────────────────────────────────────────────────────────────
def atomic_write_json(path: Path, data) -> None:
    """先写临时文件再 rename，避免中途崩溃损坏缓存。"""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Telegram（带重试）────────────────────────────────────────────────────────
def send_telegram(bot_token: str, chat_id: str, text: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            return True
        except Exception as e:
            wait = 2 ** attempt
            log.warning(f"Telegram 推送失败 (尝试 {attempt + 1}/{retries}): {e}，{wait}s 后重试")
            time.sleep(wait)
    log.error("Telegram 推送最终失败")
    return False


# ── AES 密码加密（与前端 JS 一致）────────────────────────────────────────────
_RAND_ALPHABET = string.ascii_letters + string.digits


def _rand_str(n: int) -> str:
    return "".join(secrets.choice(_RAND_ALPHABET) for _ in range(n))


def encrypt_password(password: str, salt: str) -> str:
    if not salt:
        return password
    key = salt.encode("utf-8")
    iv = _rand_str(16).encode("utf-8")
    plaintext = (_rand_str(64) + password).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(cipher.encrypt(pad(plaintext, AES.block_size))).decode()


# ── 教务系统会话 ─────────────────────────────────────────────────────────────
class SessionExpired(Exception):
    """检测到会话失效，需要重新登录。"""


class JwxtSession:
    def __init__(self, username: str, password: str, cookies_path: Path | None = None):
        self.username = username
        self.password = password
        self.cookies_path = cookies_path
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": f"{JWXT_BASE}/jwapp/sys/homeapp/home/index.html?contextPath=/jwapp",
        })
        if cookies_path and cookies_path.exists():
            self._load_cookies()

    # ------------------------------------------------------------------
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
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.session.cookies
            ]
            atomic_write_json(self.cookies_path, data)
        except Exception as e:
            log.warning(f"[{self.username}] cookies 保存失败: {e}")

    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    def login(self) -> bool:
        """完整 CAS 登录流程。"""
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
                log.error("触发验证码保护，请用浏览器手动登录一次或等几小时后重试")
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
                    self.session.get(
                        f"{CJZHCXAPP}/*default/index.do",
                        params={"THEME": "indigo", "forceApp": "cjzhcxapp"},
                        timeout=15,
                    )
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

    # ------------------------------------------------------------------
    def _post_json(self, url: str, data: dict | None = None) -> dict:
        """POST + 检测会话失效（302 跳 CAS / 401-403 / 非 JSON 响应）。"""
        r = self.session.post(url, data=data or {}, timeout=15, allow_redirects=False)
        if r.status_code in (301, 302):
            raise SessionExpired(f"被重定向到 {r.headers.get('Location', '?')}")
        if r.status_code in (401, 403):
            raise SessionExpired(f"HTTP {r.status_code}（app 上下文/会话失效）")
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise SessionExpired(f"非 JSON 响应 ({ctype})")
        return r.json()

    def fetch_all_grades(self) -> list[dict]:
        """
        一次性拉所有成绩（cjzhcxapp/wdcj/cxwdcj.do）。
        把字段规范为 {_termCode, courseNo, courseName, score, credit, gradePoint, WID}。
        """
        data = self._post_json(CXWDCJ_URL, {"pageSize": "200", "pageNumber": "1"})
        if data.get("code") != "0":
            return []
        rows = ((data.get("datas") or {}).get("cxwdcj") or {}).get("rows") or []
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


# ── 缓存 ─────────────────────────────────────────────────────────────────────
def _safe_name(name: str) -> str:
    """把用户标识转成可作文件名的安全字符串。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "default"


def cache_path_for(user_name: str) -> Path:
    return BASE_DIR / f"grades_cache.{_safe_name(user_name)}.json"


def _empty_state() -> dict:
    return {
        "scores": {},
        "failure": {"streak": 0, "first_failure_ts": None, "alert_sent": False},
    }


def load_cache(user_name: str) -> dict:
    """
    缓存结构:
    {
      "scores": { "term|courseNo": "分数" },
      "failure": {"streak": int, "first_failure_ts": float|None, "alert_sent": bool}
    }
    自动迁移旧的 grades_cache.json → grades_cache.{user}.json（仅对第一个用户）。
    """
    path = cache_path_for(user_name)
    if not path.exists() and GRADES_CACHE_FILE.exists():
        try:
            os.replace(GRADES_CACHE_FILE, path)
            log.info(f"已把旧缓存 grades_cache.json 迁移为 {path.name}")
        except OSError as e:
            log.warning(f"迁移旧缓存失败: {e}")

    if not path.exists():
        return _empty_state()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f"{path.name} 损坏，重建: {e}")
        return _empty_state()

    data.setdefault("scores", {})
    fail = data.setdefault("failure", {})
    fail.setdefault("streak", 0)
    fail.setdefault("first_failure_ts", None)
    fail.setdefault("alert_sent", False)
    data.pop("active_terms", None)  # 旧字段，清理
    return data


def save_cache(user_name: str, cache: dict) -> None:
    atomic_write_json(cache_path_for(user_name), cache)


def grade_cache_key(g: dict) -> str:
    return f"{g.get('_termCode', '')}|{g.get('courseNo', g.get('courseName', ''))}"


# ── 学期/平均分工具 ─────────────────────────────────────────────────────────
_YEAR_NAMES = ["大一", "大二", "大三", "大四", "大五", "大六", "大七"]
_SEM_NAMES = {"1": "第一学期", "2": "第二学期", "3": "小学期"}


def parse_entry_year(student_id: str) -> int:
    """从学号解析入学年份，例如 2024012616 → 2024。"""
    m = re.match(r"^(\d{4})", student_id or "")
    return int(m.group(1)) if m else 2024


def format_term(term_code: str, entry_year: int) -> str:
    """2024-2025-1 + entry_year=2024 → 大一第一学期。"""
    m = re.match(r"(\d{4})-(\d{4})-(\d+)", term_code or "")
    if not m:
        return term_code or ""
    start_year, _, sem = m.groups()
    year_idx = int(start_year) - entry_year + 1
    if 1 <= year_idx <= len(_YEAR_NAMES):
        year_name = _YEAR_NAMES[year_idx - 1]
    else:
        year_name = f"第{year_idx}学年"
    sem_name = _SEM_NAMES.get(sem, f"第{sem}学期")
    return year_name + sem_name


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def compute_weighted_avg(grades: list[dict]) -> float | None:
    """按学分加权平均；非数字成绩（如"良好"/"优秀"）不计入。"""
    total_score = 0.0
    total_credit = 0.0
    for g in grades:
        score = _to_float(g.get("score"))
        credit = _to_float(g.get("credit"))
        if score is None or credit is None or credit <= 0:
            continue
        total_score += score * credit
        total_credit += credit
    return total_score / total_credit if total_credit > 0 else None


def _enrich_with_item_scores(client: "JwxtSession", g: dict) -> None:
    """请求 details.do，把平时/期末成绩塞回 g。失败静默。"""
    if not g.get("_hasItemScores"):
        return
    try:
        d = client.fetch_grade_details(g.get("WID", ""))
    except Exception as e:
        log.warning(f"取分项成绩失败 ({g.get('courseName')}): {type(e).__name__}: {e}")
        return
    for item in d.get("itemScores") or []:
        code = item.get("code", "")
        val = item.get("value", "")
        if code == "PSCJ":
            g["usualScore"] = val
        elif code == "QMCJ":
            g["finalScore"] = val


def format_grade(g: dict, entry_year: int, old_score: str | None = None) -> str:
    name = g.get("courseName", "未知课程")
    term = g.get("_termCode", "")
    score = g.get("score", "未出")
    credit = g.get("credit", "")
    usual = g.get("usualScore")
    final = g.get("finalScore")

    lines = [f"<b>{name}</b>"]
    if term:
        lines.append(f"学期：{format_term(term, entry_year)}")
    if usual:
        lines.append(f"平时成绩：{usual}")
    if final:
        lines.append(f"期末成绩：{final}")
    if old_score is not None:
        lines.append(f"总成绩：<s>{old_score}</s> → <b>{score}</b>")
    else:
        lines.append(f"总成绩：{score}")
    if credit:
        lines.append(f"学分：{credit}")
    return "\n".join(lines)


def _send_batch(bot_token: str, chat_id: str, header: str, bodies: list[str]) -> None:
    msg = header
    for body in bodies:
        if len(msg) + len(body) + 2 > 4000:
            send_telegram(bot_token, chat_id, msg)
            msg = ""
        msg += body + "\n\n"
    if msg.strip():
        send_telegram(bot_token, chat_id, msg)


# ── 单次轮询逻辑（抽出来方便测试）────────────────────────────────────────────
def poll_once(client: JwxtSession, cache: dict, bot_token: str, chat_id: str,
              entry_year: int, user_name: str = "default") -> bool:
    """
    执行一次成绩查询 + 推送。返回 True 表示成功，False 表示需要稍后重试。
    """
    tag = f"[{user_name}]"
    try:
        grades = client.fetch_all_grades()
    except SessionExpired:
        log.info(f"{tag} 会话已过期，重新登录...")
        if not client.login():
            return False
        try:
            grades = client.fetch_all_grades()
        except Exception as e:
            log.error(f"{tag} 重新登录后仍失败: {e}")
            return False
    except Exception as e:
        log.error(f"{tag} 成绩查询异常: {type(e).__name__}: {e}")
        return False

    if not grades:
        log.warning(f"{tag} 查询返回空，本次跳过")
        return False

    scores_cache: dict = cache["scores"]
    new_grades: list[dict] = []
    updated: list[tuple[dict, str]] = []  # (新成绩字典, 旧分数)

    for g in grades:
        key = grade_cache_key(g)
        new_score = str(g.get("score", ""))
        old = scores_cache.get(key)
        if old is None:
            scores_cache[key] = new_score
            new_grades.append(g)
        elif old != new_score:
            scores_cache[key] = new_score
            updated.append((g, old))

    # 给新成绩 / 变更成绩补分项成绩（平时 + 期末）
    for g in (*new_grades, *(u[0] for u in updated)):
        _enrich_with_item_scores(client, g)

    if new_grades or updated:
        save_cache(user_name, cache)

    # Telegram 消息头里带用户名，方便多人共用一个 chat 时区分
    who = f"<b>[{user_name}]</b>"

    if new_grades:
        log.info(f"{tag} 发现 {len(new_grades)} 条新成绩")
        _send_batch(
            bot_token, chat_id,
            f"🎓 {who} 发现 {len(new_grades)} 条新成绩！\n{'─' * 20}\n",
            [format_grade(g, entry_year) for g in new_grades],
        )

    if updated:
        log.info(f"{tag} 发现 {len(updated)} 条成绩变更")
        _send_batch(
            bot_token, chat_id,
            f"🔄 {who} {len(updated)} 条成绩有变更！\n{'─' * 20}\n",
            [format_grade(g, entry_year, old) for g, old in updated],
        )

    if new_grades or updated:
        changed_terms = {g["_termCode"] for g in new_grades}
        changed_terms.update(g["_termCode"] for g, _ in updated)

        avg_lines = [f"📊 {who} 平均分统计"]
        for term in sorted(changed_terms, reverse=True):
            term_grades = [g for g in grades if g.get("_termCode") == term]
            avg = compute_weighted_avg(term_grades)
            if avg is not None:
                avg_lines.append(f"{format_term(term, entry_year)}：{avg:.2f}")
        overall = compute_weighted_avg(grades)
        if overall is not None:
            avg_lines.append(f"总平均分：{overall:.2f}")
        if len(avg_lines) > 1:
            send_telegram(bot_token, chat_id, "\n".join(avg_lines))

    if not new_grades and not updated:
        log.info(f"{tag} 暂无新成绩（共 {len(grades)} 条已知成绩）")

    return True


# ── 多用户配置 ────────────────────────────────────────────────────────────
def load_users(cfg: dict) -> list[dict]:
    """支持新 multi-user 格式（cfg.users[]）和旧 single-user 格式。
    每个 user dict 必须含 name / jwxt / telegram。"""
    if "users" in cfg:
        users = cfg["users"]
    else:
        # 旧版兼容：顶层 jwxt + telegram → 单用户
        users = [{
            "name": cfg["jwxt"]["username"],
            "jwxt": cfg["jwxt"],
            "telegram": cfg["telegram"],
        }]
    for u in users:
        u.setdefault("name", u["jwxt"]["username"])
    return users


# ── 单用户处理（一次轮询）──────────────────────────────────────────────────
# 告警阈值：必须 ≥10 次失败 且 距首次失败 ≥6 小时。暗唤醒导致的零星失败凑不齐。
ALERT_STREAK = 10
ALERT_DURATION = 6 * 3600


def process_user(user: dict) -> bool:
    """处理单个用户：登录 + 查询 + 推送 + 更新失败状态。返回 success。"""
    name = user["name"]
    jwxt = user["jwxt"]
    tg = user["telegram"]
    bot_token, chat_id = tg["bot_token"], tg["chat_id"]
    entry_year = parse_entry_year(jwxt["username"])

    log.info(f"[{name}] 开始查询成绩...")
    cache = load_cache(name)
    fail = cache["failure"]

    cookies_path = BASE_DIR / f"cookies.{_safe_name(name)}.json"
    client = JwxtSession(jwxt["username"], jwxt["password"], cookies_path)

    success = False
    try:
        # 有 cookies 直接试；没有先登。失效时 poll_once 内部会捕获并自动重登。
        if cookies_path.exists() or client.login():
            success = poll_once(client, cache, bot_token, chat_id, entry_year, name)
    except Exception as e:
        log.error(f"[{name}] 处理异常: {type(e).__name__}: {e}", exc_info=True)

    # 更新失败状态
    if success:
        if fail.get("alert_sent"):
            send_telegram(bot_token, chat_id, f"✅ [{name}] 成绩查询恢复正常")
        fail["streak"] = 0
        fail["first_failure_ts"] = None
        fail["alert_sent"] = False
    else:
        fail["streak"] = int(fail.get("streak") or 0) + 1
        if fail.get("first_failure_ts") is None:
            fail["first_failure_ts"] = time.time()
        elapsed = time.time() - fail["first_failure_ts"]
        if (not fail["alert_sent"]
                and fail["streak"] >= ALERT_STREAK
                and elapsed >= ALERT_DURATION):
            send_telegram(
                bot_token, chat_id,
                f"⚠️ [{name}] 成绩查询连续 {fail['streak']} 次失败"
                f"（已持续 {elapsed/3600:.1f} 小时），请检查日志"
            )
            fail["alert_sent"] = True
        log.warning(
            f"[{name}] 失败 {fail['streak']} 次"
            f"（已 {elapsed/60:.0f} 分钟）"
        )

    save_cache(name, cache)
    return success


# ── 入口：一次性模式 / 循环模式 ──────────────────────────────────────────
def run_once():
    """跑一遍所有用户后退出。供 launchd StartInterval 调用。"""
    cfg = load_config()
    users = load_users(cfg)
    log.info(f"=== 成绩监控启动 (PID {os.getpid()}, {len(users)} 用户) ===")
    for user in users:
        try:
            process_user(user)
        except Exception as e:
            log.error(f"[{user.get('name')}] 顶层异常: {type(e).__name__}: {e}", exc_info=True)
    log.info("=== 本轮结束 ===")


def run_loop():
    """循环模式：本机手动调试用，launchd 不需要。"""
    cfg = load_config()
    interval = int(cfg.get("interval_minutes", 20)) * 60
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，退出")
            return
        except Exception as e:
            log.error(f"循环异常: {type(e).__name__}: {e}", exc_info=True)
        log.info(f"等待 {interval//60} 分钟后再跑一轮...")
        time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        run_loop()
    else:
        run_once()
