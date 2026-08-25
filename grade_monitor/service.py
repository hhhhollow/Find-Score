"""macOS launchd service management."""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, load_config
from .storage import BASE_DIR, LOG_FILE

LAUNCHD_LABEL = "com.hhhhollow.gradeMonitor"


class ServiceError(RuntimeError):
    """launchd service management error."""


def _definition_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _target() -> str:
    return f"gui/{os.getuid()}/{LAUNCHD_LABEL}"


def _service_python() -> Path:
    """Prefer the project-local virtualenv path because it is stable across uv rebuilds."""
    project_python = BASE_DIR / ".venv" / "bin" / "python"
    if project_python.is_file() and os.access(project_python, os.X_OK):
        return project_python.absolute()
    return Path(sys.executable).absolute()


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ServiceError(detail or f"命令失败: {' '.join(command)}")
    return result


def _loaded() -> bool:
    return _run(["launchctl", "print", _target()], check=False).returncode == 0


def _definition_program(path: Path) -> list[str] | None:
    try:
        payload: Any = plistlib.loads(path.read_bytes())
    except (FileNotFoundError, OSError, plistlib.InvalidFileException):
        return None
    if not isinstance(payload, dict):
        return None
    arguments = payload.get("ProgramArguments")
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        return None
    return arguments


def render_plist(interval_minutes: int) -> bytes:
    if sys.platform != "darwin":
        raise ServiceError("简化版仅支持 macOS launchd")

    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            str(_service_python()),
            "-m",
            "grade_monitor.cli",
            "check",
        ],
        "WorkingDirectory": str(BASE_DIR),
        "EnvironmentVariables": {
            "FIND_SCORE_HOME": str(BASE_DIR),
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "StartInterval": interval_minutes * 60,
        "ProcessType": "Background",
        "ExitTimeOut": 30,
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(BASE_DIR / "launchd.stderr.log"),
    }
    return plistlib.dumps(payload, sort_keys=False)


def start_service() -> Path:
    cfg = load_config()
    path = _definition_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_plist(cfg["interval_minutes"]))
    os.chmod(path, 0o644)

    if _loaded():
        _run(["launchctl", "bootout", _target()])
    _run(["launchctl", "enable", _target()])
    _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
    return path


def stop_service() -> None:
    path = _definition_path()
    if _loaded():
        _run(["launchctl", "bootout", _target()])
    _run(["launchctl", "disable", _target()], check=False)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _definition_health(path: Path) -> tuple[bool, str]:
    arguments = _definition_program(path)
    if arguments is None:
        return False, "后台任务定义缺失或无法解析"
    if len(arguments) < 4 or arguments[1:] != ["-m", "grade_monitor.cli", "check"]:
        return False, "后台任务命令与当前 CLI 不一致，请执行 find-score restart"

    configured_python = Path(arguments[0])
    expected_python = _service_python()
    if not configured_python.is_file() or not os.access(configured_python, os.X_OK):
        return False, f"后台 Python 不可用: {configured_python}；请执行 find-score restart"
    if configured_python.absolute() != expected_python.absolute():
        return (
            False,
            f"后台 Python 已过期: {configured_python}；当前应使用 {expected_python}，请执行 find-score restart",
        )
    return True, f"后台 Python: {configured_python}"


def status_service() -> int:
    result = _run(["launchctl", "print", _target()], check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    definition_path = _definition_path()
    print(f"\n定义文件: {definition_path}")

    healthy, health_message = _definition_health(definition_path)
    print(f"运行环境: {'✅' if healthy else '⚠️'} {health_message}")

    print("\n=== 最近日志 ===")
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(lines[-15:]) or "（暂无日志）")
    except FileNotFoundError:
        print("（暂无日志）")

    if result.returncode != 0:
        return 3
    return 0 if healthy else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find-Score macOS 后台任务管理")
    parser.add_argument("action", choices=("start", "stop", "restart", "status", "render"))
    args = parser.parse_args(argv)

    try:
        if sys.platform != "darwin":
            raise ServiceError("简化版后台服务仅支持 macOS")

        if args.action == "render":
            cfg = load_config()
            sys.stdout.buffer.write(render_plist(cfg["interval_minutes"]))
            return 0
        if args.action in {"start", "restart"}:
            path = start_service()
            print(f"✅ 后台任务已加载: {path}")
            return 0
        if args.action == "stop":
            stop_service()
            print("🛑 后台任务已停止")
            return 0
        return status_service()
    except (ConfigError, ServiceError, OSError) as error:
        print(f"❌ {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
