import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grade_monitor.service import (
    LAUNCHD_LABEL,
    SYSTEMD_UNIT,
    ServiceContext,
    ServiceError,
    create_context,
    main,
    render_definition,
    render_launchd_plist,
    render_systemd_unit,
    service_status,
    start_service,
    stop_service,
)


def write_config(runtime_dir: Path) -> None:
    config = {
        "interval_minutes": 5,
        "users": [
            {
                "name": "alice",
                "jwxt": {"username": "20240001", "password": "secret"},
                "telegram": {"bot_token": "token", "chat_id": "123"},
            },
        ],
    }
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


class FakeRunner:
    def __init__(self, *, launchd_loaded: bool = False, status_code: int = 0):
        self.launchd_loaded = launchd_loaded
        self.status_code = status_code
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        *,
        check: bool,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return_code = 0
        if command[:2] == ["launchctl", "print"]:
            return_code = 0 if self.launchd_loaded else 1
        elif "status" in command:
            return_code = self.status_code
        return subprocess.CompletedProcess(command, return_code, "", "")


class ServiceRenderingTests(unittest.TestCase):
    def context(self, root: Path, system: str) -> ServiceContext:
        runtime = root / "Find Score 测试"
        runtime.mkdir()
        return ServiceContext(
            system=system,
            runtime_dir=runtime,
            python_executable=Path(sys.executable),
            home=root / "home",
            uid=501,
        )

    def test_launchd_definition_uses_argv_and_no_wake_directives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(Path(directory), "Darwin")
            definition = plistlib.loads(render_launchd_plist(context).encode())

        self.assertEqual(definition["Label"], LAUNCHD_LABEL)
        self.assertEqual(
            definition["ProgramArguments"],
            [str(Path(sys.executable)), "-m", "grade_monitor", "loop"],
        )
        self.assertEqual(definition["WorkingDirectory"], str(context.runtime_dir))
        self.assertEqual(definition["EnvironmentVariables"]["FIND_SCORE_HOME"], str(context.runtime_dir))
        self.assertTrue(definition["KeepAlive"])
        self.assertNotIn("StartInterval", definition)
        self.assertNotIn("StartCalendarInterval", definition)

    def test_systemd_definition_quotes_paths_and_has_no_timer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'space % $ " path'
            runtime.mkdir()
            context = ServiceContext(
                system="Linux",
                runtime_dir=runtime,
                python_executable=root / "python $ bin",
                home=root / "home",
                uid=1000,
            )

            unit = render_systemd_unit(context)

        self.assertIn("ExecStart=", unit)
        self.assertIn("-m", unit)
        self.assertIn("grade_monitor", unit)
        self.assertIn("Type=simple", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("UMask=0077", unit)
        self.assertIn("%%", unit)
        self.assertIn("$$", unit)
        working_line = next(line for line in unit.splitlines() if line.startswith("WorkingDirectory="))
        exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        self.assertNotIn("$" * 2, working_line)
        self.assertIn("$" * 2, exec_line)
        self.assertNotIn("OnCalendar", unit)
        self.assertNotIn("WakeSystem", unit)

    def test_linux_definition_path_respects_xdg_config_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = create_context(
                system="Linux",
                runtime_dir=root,
                python_executable=Path(sys.executable),
                home=root / "home",
                uid=1000,
                environ={"XDG_CONFIG_HOME": str(root / "xdg")},
            )

        self.assertEqual(
            context.definition_path,
            (root / "xdg" / "systemd" / "user" / SYSTEMD_UNIT).resolve(),
        )

    def test_unsupported_platform_is_explicit(self) -> None:
        context = ServiceContext(
            system="Windows",
            runtime_dir=Path("/tmp"),
            python_executable=Path(sys.executable),
            home=Path("/tmp"),
            uid=1,
        )

        with self.assertRaisesRegex(ServiceError, "不支持"):
            render_definition(context)


class ServiceLifecycleTests(unittest.TestCase):
    def make_context(self, root: Path, system: str) -> ServiceContext:
        runtime = root / "runtime"
        runtime.mkdir()
        write_config(runtime)
        return ServiceContext(
            system=system,
            runtime_dir=runtime,
            python_executable=Path(sys.executable),
            home=root / "home",
            uid=501 if system == "Darwin" else 1000,
            xdg_config_home=root / "xdg" if system == "Linux" else None,
        )

    def test_macos_start_generates_and_bootstraps_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = self.make_context(Path(directory), "Darwin")
            runner = FakeRunner()

            path = start_service(context, runner)

            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            definition = path.read_text(encoding="utf-8")
            self.assertNotIn("secret", definition)
            self.assertNotIn("token", definition)
            self.assertNotIn("20240001", definition)
            self.assertEqual(runner.commands[0][:2], ["launchctl", "print"])
            self.assertIn(["launchctl", "enable", f"gui/501/{LAUNCHD_LABEL}"], runner.commands)
            self.assertTrue(any(command[:2] == ["launchctl", "bootstrap"] for command in runner.commands))

    def test_macos_update_boots_out_loaded_agent_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = self.make_context(Path(directory), "Darwin")
            runner = FakeRunner(launchd_loaded=True)

            start_service(context, runner)

        self.assertEqual(runner.commands[1][:2], ["launchctl", "bootout"])

    def test_linux_start_and_stop_use_user_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = self.make_context(Path(directory), "Linux")
            runner = FakeRunner()

            path = start_service(context, runner)
            self.assertTrue(path.exists())
            self.assertEqual(
                runner.commands[-3:],
                [
                    ["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", SYSTEMD_UNIT],
                    ["systemctl", "--user", "restart", SYSTEMD_UNIT],
                ],
            )

            stop_service(context, runner)

            self.assertFalse(path.exists())
            self.assertIn(
                ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT],
                runner.commands,
            )

    def test_status_exit_code_tracks_native_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = self.make_context(Path(directory), "Linux")

            self.assertEqual(service_status(context, FakeRunner(status_code=0)), 0)
            self.assertEqual(service_status(context, FakeRunner(status_code=3)), 3)

    @patch("grade_monitor.service.create_context")
    @patch("grade_monitor.service.start_service")
    def test_service_cli_dispatches_start(self, start, create) -> None:
        create.return_value = ServiceContext(
            system="Linux",
            runtime_dir=Path("/tmp/runtime"),
            python_executable=Path(sys.executable),
            home=Path("/tmp/home"),
            uid=1000,
        )
        start.return_value = Path("/tmp/unit")

        self.assertEqual(main(["start"]), 0)
        start.assert_called_once_with(create.return_value)


if __name__ == "__main__":
    unittest.main()
