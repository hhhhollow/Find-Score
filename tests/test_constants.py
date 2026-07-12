import tempfile
import unittest
from pathlib import Path

from grade_monitor.constants import resolve_runtime_dir


class RuntimeDirectoryTests(unittest.TestCase):
    def test_installed_package_uses_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_root = root / "site-packages"
            working_dir = root / "project"
            package_root.mkdir()
            working_dir.mkdir()

            resolved = resolve_runtime_dir(package_root, working_dir, {})

            self.assertEqual(resolved, working_dir.resolve())

    def test_source_checkout_with_config_uses_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            working_dir = root / "elsewhere"
            source_root.mkdir()
            working_dir.mkdir()
            (source_root / "config.json").touch()

            resolved = resolve_runtime_dir(source_root, working_dir, {})

            self.assertEqual(resolved, source_root.resolve())

    def test_environment_override_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = root / "data"

            resolved = resolve_runtime_dir(
                root / "package",
                root / "cwd",
                {"FIND_SCORE_HOME": str(configured)},
            )

            self.assertEqual(resolved, configured.resolve())


if __name__ == "__main__":
    unittest.main()
