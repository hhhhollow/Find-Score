import tempfile
import unittest
from pathlib import Path

from grade_monitor.locking import InstanceAlreadyRunning, instance_lock


class InstanceLockTests(unittest.TestCase):
    def test_prevents_second_process_slot_and_uses_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.lock"

            with instance_lock(path):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(InstanceAlreadyRunning):
                    with instance_lock(path):
                        pass

            with instance_lock(path):
                pass


if __name__ == "__main__":
    unittest.main()
