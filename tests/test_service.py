import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grade_monitor import service


class LaunchdRuntimeTests(unittest.TestCase):
    def test_render_prefers_stable_project_virtualenv_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_python = root / ".venv" / "bin" / "python"
            project_python.parent.mkdir(parents=True)
            project_python.write_text("#!/bin/sh\n", encoding="utf-8")
            project_python.chmod(0o755)

            with (
                patch.object(service, "BASE_DIR", root),
                patch.object(service.sys, "platform", "darwin"),
            ):
                payload = plistlib.loads(service.render_plist(20))

        self.assertEqual(payload["ProgramArguments"][0], str(project_python.absolute()))
        self.assertEqual(
            payload["ProgramArguments"][1:],
            ["-m", "grade_monitor.cli", "check"],
        )

    def test_definition_health_rejects_missing_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definition = root / "agent.plist"
            missing_python = root / "missing-python"
            definition.write_bytes(
                plistlib.dumps(
                    {
                        "ProgramArguments": [
                            str(missing_python),
                            "-m",
                            "grade_monitor.cli",
                            "check",
                        ]
                    }
                )
            )

            with patch.object(service, "BASE_DIR", root):
                healthy, message = service._definition_health(definition)

        self.assertFalse(healthy)
        self.assertIn("后台 Python 不可用", message)
        self.assertIn("find-score restart", message)

    def test_definition_health_rejects_stale_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / ".venv" / "bin" / "python"
            expected.parent.mkdir(parents=True)
            expected.write_text("#!/bin/sh\n", encoding="utf-8")
            expected.chmod(0o755)

            stale = root / "old-python"
            stale.write_text("#!/bin/sh\n", encoding="utf-8")
            stale.chmod(0o755)

            definition = root / "agent.plist"
            definition.write_bytes(
                plistlib.dumps(
                    {
                        "ProgramArguments": [
                            str(stale),
                            "-m",
                            "grade_monitor.cli",
                            "check",
                        ]
                    }
                )
            )

            with patch.object(service, "BASE_DIR", root):
                healthy, message = service._definition_health(definition)

        self.assertFalse(healthy)
        self.assertIn("后台 Python 已过期", message)
        self.assertIn(str(expected), message)

    def test_service_python_falls_back_to_current_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(service, "BASE_DIR", root):
                self.assertEqual(
                    service._service_python(),
                    Path(sys.executable).absolute(),
                )


if __name__ == "__main__":
    unittest.main()
