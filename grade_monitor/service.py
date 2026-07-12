"""macOS launchd / Linux systemd-user 服务安装与管理。"""

import argparse
import os
import platform
import plistlib
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, load_config
from .constants import BASE_DIR
from .storage import atomic_write_text

# 保留旧版 label，避免升级后同时存在两个 LaunchAgent。
LAUNCHD_LABEL = "com.hhhhollow.gradeMonitor"
SYSTEMD_UNIT = "find-score.service"
Runner = Callable[..., subprocess.CompletedProcess[str]]


class ServiceError(RuntimeError):
    """服务平台、定义或系统管理命令错误。"""


@dataclass(frozen=True, slots=True)
class ServiceContext:
    system: str
    runtime_dir: Path
    python_executable: Path
    home: Path
    uid: int
    xdg_config_home: Path | None = None

    @property
    def definition_path(self) -> Path:
        if self.system == "Darwin":
            return self.home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if self.system == "Linux":
            config_home = self.xdg_config_home or self.home / ".config"
            return config_home / "systemd" / "user" / SYSTEMD_UNIT
        raise ServiceError(f"不支持的操作系统: {self.system}")


def create_context(
    *,
    system: str | None = None,
    runtime_dir: Path | None = None,
    python_executable: Path | None = None,
    home: Path | None = None,
    uid: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> ServiceContext:
    """从当前已激活的 Python 环境构建服务上下文。"""
    environment = os.environ if environ is None else environ
    executable = python_executable or Path(os.path.abspath(sys.executable))
    configured_xdg = environment.get("XDG_CONFIG_HOME")
    return ServiceContext(
        system=platform.system() if system is None else system,
        runtime_dir=(BASE_DIR if runtime_dir is None else runtime_dir).resolve(),
        python_executable=Path(os.path.abspath(executable)),
        home=(Path.home() if home is None else home).resolve(),
        uid=os.getuid() if uid is None else uid,
        xdg_config_home=(
            Path(configured_xdg).expanduser().resolve() if configured_xdg else None
        ),
    )


def _systemd_quote(value: str, *, escape_dollar: bool = False) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ServiceError("systemd 参数不能包含 NUL 或换行")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if escape_dollar:
        # systemd 仅在 ExecStart 等命令行中把 $ 解释为环境变量引用。
        escaped = escaped.replace("$", "$$")
    return f'"{escaped}"'


def render_launchd_plist(context: ServiceContext) -> str:
    """生成不经 shell 的 LaunchAgent plist。"""
    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            str(context.python_executable),
            "-m",
            "grade_monitor",
            "loop",
        ],
        "WorkingDirectory": str(context.runtime_dir),
        "EnvironmentVariables": {
            "FIND_SCORE_HOME": str(context.runtime_dir),
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "ProcessType": "Background",
        "ExitTimeOut": 30,
        "Umask": 0o077,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(context.runtime_dir / "launchd.stderr.log"),
    }
    return plistlib.dumps(payload, sort_keys=False).decode("utf-8")


def render_systemd_unit(context: ServiceContext) -> str:
    """生成 systemd user unit，安全处理空格和特殊字符。"""
    command = " ".join(
        _systemd_quote(value, escape_dollar=True)
        for value in (
            str(context.python_executable),
            "-m",
            "grade_monitor",
            "loop",
        )
    )
    return "\n".join(
        [
            "[Unit]",
            "Description=BISTU grade monitor",
            "StartLimitIntervalSec=5min",
            "StartLimitBurst=5",
            "",
            "[Service]",
            # Type=simple also works on systemd releases older than 240.
            "Type=simple",
            f"WorkingDirectory={_systemd_quote(str(context.runtime_dir))}",
            f"Environment={_systemd_quote(f'FIND_SCORE_HOME={context.runtime_dir}')}",
            'Environment="PYTHONUNBUFFERED=1"',
            f"ExecStart={command}",
            "Restart=always",
            "RestartSec=60",
            "TimeoutStopSec=30",
            "UMask=0077",
            "NoNewPrivileges=yes",
            "StandardOutput=journal",
            "StandardError=journal",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ],
    )


def render_definition(context: ServiceContext) -> str:
    if context.system == "Darwin":
        return render_launchd_plist(context)
    if context.system == "Linux":
        return render_systemd_unit(context)
    raise ServiceError(f"不支持的操作系统: {context.system}")


def _invoke(
    runner: Runner,
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            command,
            check=False,
            text=True,
            capture_output=capture_output,
        )
    except FileNotFoundError as error:
        raise ServiceError(f"未找到系统命令: {command[0]}") from error
    if check and result.returncode != 0:
        detail = (result.stderr or "").strip() if capture_output else ""
        suffix = f": {detail}" if detail else ""
        raise ServiceError(f"命令执行失败 ({result.returncode}): {' '.join(command)}{suffix}")
    return result


def _validate_start(context: ServiceContext) -> None:
    if context.system not in {"Darwin", "Linux"}:
        raise ServiceError(f"不支持的操作系统: {context.system}")
    if not context.python_executable.is_file():
        raise ServiceError(f"Python 解释器不存在: {context.python_executable}")
    for path in (context.runtime_dir, context.python_executable, context.definition_path):
        if any(character in str(path) for character in ("\x00", "\n", "\r")):
            raise ServiceError(f"路径不能包含 NUL 或换行: {path}")
    load_config(context.runtime_dir / "config.json")


def _launchd_target(context: ServiceContext) -> str:
    return f"gui/{context.uid}/{LAUNCHD_LABEL}"


def _launchd_loaded(context: ServiceContext, runner: Runner) -> bool:
    result = _invoke(
        runner,
        ["launchctl", "print", _launchd_target(context)],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def start_service(
    context: ServiceContext,
    runner: Runner = subprocess.run,
) -> Path:
    """安装（或更新）定义并启动服务。"""
    _validate_start(context)
    definition_path = context.definition_path
    launchd_loaded = False
    if context.system == "Darwin":
        launchd_loaded = _launchd_loaded(context, runner)
    elif context.system == "Linux":
        # 写入 unit 前先确认当前会话可连接 systemd user manager。
        _invoke(
            runner,
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
        )
    atomic_write_text(definition_path, render_definition(context), mode=0o644)

    if context.system == "Darwin":
        target = _launchd_target(context)
        if launchd_loaded:
            _invoke(runner, ["launchctl", "bootout", target])
        _invoke(runner, ["launchctl", "enable", target])
        _invoke(
            runner,
            ["launchctl", "bootstrap", f"gui/{context.uid}", str(definition_path)],
        )
    elif context.system == "Linux":
        _invoke(runner, ["systemctl", "--user", "daemon-reload"])
        _invoke(runner, ["systemctl", "--user", "enable", SYSTEMD_UNIT])
        _invoke(runner, ["systemctl", "--user", "restart", SYSTEMD_UNIT])
    else:
        raise ServiceError(f"不支持的操作系统: {context.system}")
    return definition_path


def stop_service(
    context: ServiceContext,
    runner: Runner = subprocess.run,
) -> None:
    """停止、禁用并移除当前用户的服务定义。"""
    definition_path = context.definition_path
    if context.system == "Darwin":
        if _launchd_loaded(context, runner):
            _invoke(runner, ["launchctl", "bootout", _launchd_target(context)])
        _invoke(
            runner,
            ["launchctl", "disable", _launchd_target(context)],
            check=False,
            capture_output=True,
        )
    elif context.system == "Linux":
        command = ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT]
        _invoke(
            runner,
            command,
            check=definition_path.exists(),
            capture_output=True,
        )
    else:
        raise ServiceError(f"不支持的操作系统: {context.system}")

    try:
        definition_path.unlink()
    except FileNotFoundError:
        pass
    if context.system == "Linux":
        _invoke(runner, ["systemctl", "--user", "daemon-reload"])


def service_status(
    context: ServiceContext,
    runner: Runner = subprocess.run,
) -> int:
    """显示原生服务状态；运行中返回 0，否则返回 3。"""
    if context.system == "Darwin":
        result = _invoke(
            runner,
            ["launchctl", "print", _launchd_target(context)],
            check=False,
            capture_output=True,
        )
    elif context.system == "Linux":
        result = _invoke(
            runner,
            ["systemctl", "--user", "status", "--no-pager", SYSTEMD_UNIT],
            check=False,
            capture_output=True,
        )
    else:
        raise ServiceError(f"不支持的操作系统: {context.system}")
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode == 0:
        return 0
    definition_exists = context.definition_path.exists()
    if definition_exists:
        print("⚠️ 后台服务已安装，但当前未运行")
    else:
        print("🛑 后台服务未安装或未运行")
    if definition_exists and result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return 3


def _print_recent_log(runtime_dir: Path, lines: int = 15) -> None:
    log_path = runtime_dir / "grade_monitor.log"
    print(f"\n=== 最近 {lines} 行应用日志 ===")
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        print("（暂无日志）")
        return
    print("\n".join(content[-lines:]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find-Score 跨平台后台服务管理")
    parser.add_argument("action", choices=("start", "stop", "restart", "status", "render"))
    args = parser.parse_args(argv)

    try:
        context = create_context()
        if args.action == "render":
            print(render_definition(context), end="")
            return 0
        if args.action == "start":
            path = start_service(context)
            print(f"✅ 后台服务已启动: {path}")
            return 0
        if args.action == "restart":
            path = start_service(context)
            print(f"🔄 后台服务已重启: {path}")
            return 0
        if args.action == "stop":
            stop_service(context)
            print("🛑 后台服务已停止并禁用")
            return 0

        code = service_status(context)
        print(f"\n定义文件: {context.definition_path}")
        _print_recent_log(context.runtime_dir)
        return code
    except (ConfigError, ServiceError, OSError) as error:
        print(f"❌ {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
