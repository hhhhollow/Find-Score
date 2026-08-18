import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grade_monitor import cache


class CacheTests(unittest.TestCase):
    def test_legacy_cache_is_never_migrated_to_later_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "grades_cache.json"
            legacy.write_text(json.dumps({"scores": {"x": "90"}}), encoding="utf-8")

            def path_for(name: str) -> Path:
                return root / f"grades_cache.{name}.json"

            path_for("first").write_text(
                json.dumps({"scores": {"first": "80"}}),
                encoding="utf-8",
            )
            with (
                patch.object(cache, "GRADES_CACHE_FILE", legacy),
                patch.object(cache, "cache_path_for", side_effect=path_for),
            ):
                first = cache.load_cache("first", migrate_legacy=True)
                second = cache.load_cache("second", migrate_legacy=False)

            self.assertEqual(first["scores"], {"first": "80"})
            self.assertEqual(second["scores"], {})
            self.assertTrue(legacy.exists())

    def test_old_cache_is_normalized_to_current_schema(self) -> None:
        normalized = cache._normalize_state(
            {"scores": {"course": 90}, "failure": {"streak": "2"}},
            Path("old.json"),
        )

        self.assertEqual(normalized["version"], cache.CACHE_VERSION)
        self.assertTrue(normalized["initialized"])
        self.assertEqual(normalized["scores"], {"course": "90"})
        self.assertIsNone(normalized["outbox"])

    def test_saved_cache_has_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grades_cache.user.json"
            with patch.object(cache, "cache_path_for", return_value=path):
                cache.save_cache("user", cache._empty_state())

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
