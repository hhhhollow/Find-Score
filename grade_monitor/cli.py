"""Unified command-line interface for Find-Score."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from .__main__ import main as check_main
from .config import ConfigError, load_config
from .service import main as service_main
from .storage import CONFIG_FILE, LOG_FILE

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
        print(f"状态: 无效 ({error})", file=sys.stderr)
        return 1

    print("状态: 有效")
    print(f"学号: {_mask_username(cfg['jwxt']['username'])}")
    print(f"查询间隔: {cfg['interval_minutes']} 分钟")
    bark = cfg["bark"]
    print(f"Bark: {bark['server']} | {bark['group']} | {bark['sound']}")
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

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
