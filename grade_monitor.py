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
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": f"{JWXT_BASE}/jwapp/sys/homeapp/home/index.html?contextPath=/jwapp",
        })
        self._logged_in = False

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
                self._logged_in = True
                log.info("CAS 登录成功")
                # 注册 cjzhcxapp 应用上下文（不然 cxwdcj.do 会 403）
                try:
                    self.session.get(
                        f"{CJZHCXAPP}/*default/index.do",
                        params={"THEME": "indigo", "forceApp": "cjzhcxapp"},
                        timeout=15,
                    )
                except Exception as e:
                    log.warning(f"访问 cjzhcxapp 入口失败（可能不影响）: {e}")
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
    def _request_json(self, url: str, params: dict | None = None) -> dict:
        """统一封装：检测会话失效抛 SessionExpired。"""
        r = self.session.get(url, params=params, timeout=15, allow_redirects=False)
        # 会话失效时教务系统通常 302 跳到 CAS
        if r.status_code in (301, 302):
            raise SessionExpired(f"被重定向到 {r.headers.get('Location', '?')}")
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise SessionExpired(f"非 JSON 响应 ({ctype})")
        return r.json()

    def _post_json(self, url: str, data: dict | None = None) -> dict:
        """POST 版的 _request_json：检测会话失效。"""
        r = self.session.post(url, data=data or {}, timeout=15, allow_redirects=False)
        if r.status_code in (301, 302):
            raise SessionExpired(f"被重定向到 {r.headers.get('Location', '?')}")
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
        out: list[dict] = []
        for r in rows:
            out.append({
                "_termCode": r.get("XNXQDM", ""),
                "courseNo": r.get("KCH", ""),
                "courseName": r.get("KCM", "未知课程"),
                "score": r.get("XSZCJ", ""),
                "credit": r.get("XF", ""),
                "gradePoint": r.get("JD", ""),
                "WID": r.get("WID", ""),
                "_hasItemScores": bool(r.get("FXCJ")),
            })
        return out

    def fetch_grade_details(self, wid: str) -> dict:
        """拉单门课的分项成绩 → 返回 details 子对象，含 itemScores[]。"""
        if not wid:
            return {}
        data = self._post_json(DETAILS_URL, {"WID": wid})
        if data.get("code") != "0":
            return {}
        return (data.get("datas") or {}).get("details") or {}


# ── 缓存 ─────────────────────────────────────────────────────────────────────
def load_cache() -> dict:
    """
    缓存结构:
    {
      "scores": { "term|courseNo": "分数" },
      "active_terms": ["2025-2026-1", "2024-2025-2", ...]
    }
    """
    if not GRADES_CACHE_FILE.exists():
        return {"scores": {}, "active_terms": []}
    try:
        with open(GRADES_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # 兼容旧版本（顶层就是 scores dict）
        if "scores" not in data:
            return {"scores": data, "active_terms": []}
        data.setdefault("scores", {})
        data.setdefault("active_terms", [])
        return data
    except Exception as e:
        log.warning(f"缓存文件损坏，重建: {e}")
        return {"scores": {}, "active_terms": []}


def save_cache(cache: dict) -> None:
    atomic_write_json(GRADES_CACHE_FILE, cache)


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


def _pick(g: dict, *keys) -> str | None:
    """从多个候选字段名中取第一个非空值。"""
    for k in keys:
        v = g.get(k)
        if v not in (None, "", "-"):
            return str(v)
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
    usual = _pick(g, "pscj", "usualScore", "regularScore", "ordinaryScore", "psbfb")
    final = _pick(g, "qmcj", "finalScore", "endTermScore", "examScore", "qmbfb")

    lines = [f"<b>{name}</b>"]
    if term:
        lines.append(f"学期：{format_term(term, entry_year)}")
    if usual is not None:
        lines.append(f"平时成绩：{usual}")
    if final is not None:
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
              entry_year: int) -> bool:
    """
    执行一次成绩查询 + 推送。返回 True 表示成功，False 表示需要稍后重试。
    """
    # 尝试直接用现有 Session 查询，会话失效则重新登录
    try:
        grades = client.fetch_all_grades()
    except SessionExpired:
        log.info("会话已过期，重新登录...")
        if not client.login():
            return False
        try:
            grades = client.fetch_all_grades()
        except Exception as e:
            log.error(f"重新登录后仍失败: {e}")
            return False
    except Exception as e:
        log.error(f"成绩查询异常: {type(e).__name__}: {e}")
        return False

    if not grades:
        log.warning("查询返回空，可能是接口异常，本次跳过")
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
    for g in new_grades:
        _enrich_with_item_scores(client, g)
    for g, _ in updated:
        _enrich_with_item_scores(client, g)

    # 更新 active_terms：有数据的学期列表（兼容旧缓存结构）
    new_active_terms = sorted({g["_termCode"] for g in grades}, reverse=True)
    active_terms_changed = new_active_terms != cache.get("active_terms", [])
    cache["active_terms"] = new_active_terms

    if new_grades or updated or active_terms_changed:
        save_cache(cache)

    if new_grades:
        log.info(f"发现 {len(new_grades)} 条新成绩")
        _send_batch(
            bot_token, chat_id,
            f"🎓 发现 {len(new_grades)} 条新成绩！\n{'─' * 20}\n",
            [format_grade(g, entry_year) for g in new_grades],
        )

    if updated:
        log.info(f"发现 {len(updated)} 条成绩变更")
        _send_batch(
            bot_token, chat_id,
            f"🔄 {len(updated)} 条成绩有变更！\n{'─' * 20}\n",
            [format_grade(g, entry_year, old) for g, old in updated],
        )

    if new_grades or updated:
        changed_terms = {g["_termCode"] for g in new_grades}
        changed_terms.update(g["_termCode"] for g, _ in updated)

        avg_lines = ["📊 <b>平均分统计</b>"]
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
        log.info(f"暂无新成绩（共 {len(grades)} 条已知成绩）")

    return True


# ── 主循环 ───────────────────────────────────────────────────────────────────
def run():
    cfg = load_config()
    tg = cfg["telegram"]
    bot_token, chat_id = tg["bot_token"], tg["chat_id"]
    interval_min = int(cfg.get("interval_minutes", 20))
    interval = interval_min * 60

    cache = load_cache()
    client = JwxtSession(cfg["jwxt"]["username"], cfg["jwxt"]["password"])
    entry_year = parse_entry_year(cfg["jwxt"]["username"])

    log.info(f"成绩监控启动 (PID {os.getpid()})，查询间隔 {interval_min} 分钟")
    send_telegram(
        bot_token, chat_id,
        f"✅ 成绩监控已启动\n每 {interval_min} 分钟查询一次"
    )

    # 首次登录
    if not client.login():
        send_telegram(bot_token, chat_id, "❌ 启动时首次登录失败，请检查账号密码")
        sys.exit(1)

    backoff = 30           # 失败后的退避秒数（指数）
    max_backoff = 1800     # 上限 30 分钟
    failure_streak = 0

    while True:
        try:
            log.info("开始查询成绩...")
            success = poll_once(client, cache, bot_token, chat_id, entry_year)

            if success:
                failure_streak = 0
                backoff = 30
                wait = interval
                log.info(f"等待 {interval_min} 分钟后再次查询...")
            else:
                failure_streak += 1
                if failure_streak == 5:
                    send_telegram(
                        bot_token, chat_id,
                        f"⚠️ 连续 {failure_streak} 次查询失败，请检查日志"
                    )
                wait = min(backoff, max_backoff)
                backoff *= 2
                log.warning(f"失败 {failure_streak} 次，{wait}s 后重试")

            time.sleep(wait)

        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，退出")
            send_telegram(bot_token, chat_id, "⏹ 成绩监控已停止")
            break
        except Exception as e:
            log.error(f"主循环未捕获异常: {type(e).__name__}: {e}", exc_info=True)
            time.sleep(60)  # 未知异常稍等再继续


if __name__ == "__main__":
    run()
