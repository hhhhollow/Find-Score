"""Unified command-line interface for Find-Score."""

import argparse
import sys
import json
import time
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

import requests

from .__main__ import main as check_main
from .config import ConfigError, load_config
from .notify import send_bark
from .service import main as service_main
from .storage import CONFIG_FILE, COOKIES_FILE, LOG_FILE, atomic_write_json, load_status

_SERVICE_ACTIONS = ("start", "stop", "restart", "status", "render")


def _version() -> str:
    try:
        return version("find-score")
    except PackageNotFoundError:
        return "dev"


def _mask_username(username: str) -> str:
    if len(username) <= 4:
        return "*" * len(username)
    prefix_len = min(4, max(1, len(username) - 3))
    hidden_len = len(username) - prefix_len - 2
    return f"{username[:prefix_len]}{'*' * hidden_len}{username[-2:]}"


def _show_config() -> int:
    print(f"配置文件: {CONFIG_FILE}")
    try:
        cfg = load_config()
    except (ConfigError, OSError) as error:
        print(f"配置状态: 无效 ({error})", file=sys.stderr)
        return 1

    print("配置状态: 有效")
    print(f"学号: {_mask_username(cfg['jwxt']['username'])}")
    print(f"查询间隔: {cfg['interval_minutes']} 分钟")
    bark = cfg["bark"]
    print(f"Bark: {bark['server']} | {bark['group']} | {bark['sound']}")

    status = load_status()
    if status:
        state = status.get("status", "UNKNOWN")
        icon = "🟢" if state == "HEALTHY" else "🔴"
        print(f"健康状态: {icon} {state}")
        if status.get("last_success_at"):
            print(f"上次成功: {status['last_success_at']}")
        if status.get("last_error"):
            print(f"最近异常: {status['last_error']}")
    return 0


def _show_logs(lines: int) -> int:
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        print(f"暂无日志: {LOG_FILE}")
        return 0
    except OSError as error:
        print(f"读取日志失败: {error}", file=sys.stderr)
        return 1

    selected = content[-lines:] if lines else content
    if selected:
        print("\n".join(selected))
    return 0


def _test_notify() -> int:
    try:
        cfg = load_config()
    except (ConfigError, OSError) as error:
        print(f"❌ 配置加载失败: {error}", file=sys.stderr)
        return 1

    bark = cfg["bark"]
    print(f"正在向 Bark 发送测试通知: {bark['server']} (Group: {bark['group']})...")
    ok = send_bark(
        bark["key"],
        "这是一条来自 Find-Score 的测试通知，通道工作正常！🎉",
        title="Find-Score 测试通知",
        server=bark["server"],
        group=bark["group"],
        sound=bark["sound"],
    )
    if ok:
        print("✅ Bark 测试通知发送成功！请检查手机端 Bark。")
        return 0
    else:
        print("❌ Bark 测试通知发送失败，请检查 key 与网络连接。", file=sys.stderr)
        return 1


def _parse_cookie_input(raw: str) -> list[dict[str, str]]:
    raw = raw.strip()
    if not raw:
        raise ValueError("输入的 Cookie 不能为空")

    if raw.startswith("[") or raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                result: list[dict[str, str]] = []
                for item in data:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        result.append(
                            {
                                "name": str(item["name"]),
                                "value": str(item["value"]),
                                "domain": str(item.get("domain", "jwxt.bistu.edu.cn")),
                                "path": str(item.get("path", "/")),
                            }
                        )
                if result:
                    return result
            elif isinstance(data, dict):
                return [
                    {
                        "name": str(k),
                        "value": str(v),
                        "domain": "jwxt.bistu.edu.cn",
                        "path": "/",
                    }
                    for k, v in data.items()
                ]
        except json.JSONDecodeError:
            pass

    result: list[dict[str, str]] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, val = part.split("=", 1)
        result.append(
            {
                "name": key.strip(),
                "value": val.strip(),
                "domain": "jwxt.bistu.edu.cn",
                "path": "/",
            }
        )
    if not result:
        raise ValueError("未能解析出任何有效 Cookie，请检查格式")
    return result


def _verify_cookies(cookies_list: list[dict[str, str]]) -> bool:
    s = requests.Session()
    for c in cookies_list:
        s.cookies.set(
            c["name"],
            c["value"],
            domain=c.get("domain", "jwxt.bistu.edu.cn"),
            path=c.get("path", "/"),
        )
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://jwxt.bistu.edu.cn/jwapp/sys/homeapp/home/index.html?contextPath=/jwapp",
        }
    )
    try:
        r = s.post(
            "https://jwxt.bistu.edu.cn/jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do",
            data={"pageSize": "1", "pageNumber": "1"},
            timeout=10,
            allow_redirects=False,
        )
        if r.status_code == 200 and "json" in r.headers.get("Content-Type", "").lower():
            payload = r.json()
            return payload.get("code") == "0"
    except Exception:
        pass
    return False


def _handle_cookie(args: argparse.Namespace) -> int:
    if getattr(args, "clear", False):
        if COOKIES_FILE.exists():
            COOKIES_FILE.unlink()
            print(f"🗑️ 已删除本地 Cookie 文件: {COOKIES_FILE}")
        else:
            print(f"本地 Cookie 文件不存在: {COOKIES_FILE}")
        return 0

    if getattr(args, "import_cookie", False) or getattr(args, "raw_cookie", None):
        raw = getattr(args, "raw_cookie", None)
        if not raw:
            print("请输入浏览器中的教务系统 Cookie（支持 JSON 数组或 'k1=v1; k2=v2' 格式）：")
            try:
                raw = input().strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已取消输入。")
                return 1

        try:
            parsed = _parse_cookie_input(raw)
        except ValueError as err:
            print(f"❌ 解析失败: {err}", file=sys.stderr)
            return 1

        atomic_write_json(COOKIES_FILE, parsed)
        print(f"💾 已成功保存 {len(parsed)} 个 Cookie 至: {COOKIES_FILE}")

        print("正在校验 Cookie 有效性...")
        if _verify_cookies(parsed):
            print("✅ 校验成功：Cookie 有效，教务系统鉴权通过！")
        else:
            print("⚠️ 提示：教务系统接口返回未登录或跳转，Cookie 可能已失效或缺少必要字段。")
        return 0

    # Default: show cookie status
    print(f"Cookie 文件: {COOKIES_FILE}")
    if not COOKIES_FILE.is_file():
        print("状态: 未创建 (尚未登录或已清理)")
        return 0

    try:
        with open(COOKIES_FILE, encoding="utf-8") as file:
            data = json.load(file)
    except Exception as err:
        print(f"状态: 文件损坏 ({err})", file=sys.stderr)
        return 1

    if not isinstance(data, list):
        print("状态: 格式无效 (必须为 JSON 列表)")
        return 1

    names = [c.get("name") for c in data if isinstance(c, dict) and "name" in c]
    has_weu = "_WEU" in names
    has_gssid = "GS_SESSIONID" in names
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(COOKIES_FILE.stat().st_mtime))

    print(f"最后更新: {mtime}")
    print(f"包含项 ({len(names)}): {', '.join(str(n) for n in names)}")
    print(f"关键凭据: _WEU={'✅' if has_weu else '❌'}, GS_SESSIONID={'✅' if has_gssid else '❌'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="find-score",
        description="BISTU 成绩查询与 macOS 后台监控",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("check", help="立即查询一次成绩")
    for action in _SERVICE_ACTIONS:
        subparsers.add_parser(action, help=f"后台任务: {action}")

    logs = subparsers.add_parser("logs", help="查看应用日志")
    logs.add_argument("-n", "--lines", type=int, default=30, help="显示最后 N 行（默认 30；0 表示全部）")
    subparsers.add_parser("config", help="检查配置并显示非敏感摘要")
    subparsers.add_parser("test-notify", help="向 Bark 发送一条测试通知")

    cookie_parser = subparsers.add_parser("cookie", help="查看、导入或清理本地会话 Cookie")
    cookie_parser.add_argument(
        "-i", "--import",
        dest="import_cookie",
        action="store_true",
        help="导入 Cookie",
    )
    cookie_parser.add_argument(
        "raw_cookie",
        nargs="?",
        default=None,
        help="待导入的 Cookie 字符串（JSON 或 'k=v; ...'）",
    )
    cookie_parser.add_argument(
        "--clear",
        action="store_true",
        help="删除本地已保存的 Cookie",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "check"):
        return check_main()
    if args.command in _SERVICE_ACTIONS:
        return service_main([args.command])
    if args.command == "logs":
        if args.lines < 0:
            parser.error("--lines 不能为负数")
        return _show_logs(args.lines)
    if args.command == "config":
        return _show_config()
    if args.command == "test-notify":
        return _test_notify()
    if args.command == "cookie":
        return _handle_cookie(args)

    parser.error(f"未知命令: {args.command}")
    return 2



if __name__ == "__main__":
    raise SystemExit(main())
