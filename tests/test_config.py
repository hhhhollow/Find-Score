import json
import tempfile
import unittest
from pathlib import Path

from grade_monitor.config import ConfigError, load_config, load_users


def user(name: str = "alice") -> dict:
    return {
        "name": name,
        "jwxt": {"username": "20240001", "password": "secret"},
        "telegram": {"bot_token": "token", "chat_id": "123"},
    }


class ConfigTests(unittest.TestCase):
    def test_load_config_normalizes_interval_and_copies_users(self) -> None:
        raw = {"interval_minutes": "15", "users": [user()]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(path)

        self.assertEqual(config["interval_minutes"], 15)
        self.assertEqual(config["users"][0]["name"], "alice")

    def test_legacy_single_user_format(self) -> None:
        legacy = {
            "jwxt": {"username": "20240001", "password": "secret"},
            "telegram": {"bot_token": "token", "chat_id": "123"},
        }

        users = load_users(legacy)

        self.assertEqual(users[0]["name"], "20240001")

    def test_rejects_storage_name_collision(self) -> None:
        with self.assertRaisesRegex(ConfigError, "同一缓存文件"):
            load_users({"users": [user("a/b"), user("a?b")]})

    def test_rejects_invalid_interval(self) -> None:
        raw = {"interval_minutes": 0, "users": [user()]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "大于 0"):
                load_config(path)

    def test_rejects_fractional_interval(self) -> None:
        raw = {"interval_minutes": 1.9, "users": [user()]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "正整数"):
                load_config(path)

    def test_does_not_mutate_input(self) -> None:
        raw_user = user()
        config = {"users": [raw_user]}

        normalized = load_users(config)
        normalized[0]["name"] = "changed"

        self.assertEqual(raw_user["name"], "alice")

    def test_preserves_password_spaces_and_accepts_numeric_chat_id(self) -> None:
        raw_user = user()
        raw_user["jwxt"]["password"] = "  secret  "
        raw_user["telegram"]["chat_id"] = 123

        normalized = load_users({"users": [raw_user]})[0]

        self.assertEqual(normalized["jwxt"]["password"], "  secret  ")
        self.assertEqual(normalized["telegram"]["chat_id"], "123")


    def test_supports_bark_notification_config(self) -> None:
        bark_user = {
            "name": "bob",
            "jwxt": {"username": "20240002", "password": "pwd"},
            "bark": {"key": "mykey", "sound": "bell"},
        }
        normalized = load_users({"users": [bark_user]})[0]
        self.assertIn("bark", normalized)
        self.assertEqual(normalized["bark"]["key"], "mykey")
        self.assertEqual(normalized["bark"]["server"], "https://api.day.app")

    def test_supports_bark_url_string(self) -> None:
        bark_user = {
            "name": "charlie",
            "jwxt": {"username": "20240003", "password": "pwd"},
            "bark": "https://api.day.app/mykey/",
        }
        normalized = load_users({"users": [bark_user]})[0]
        self.assertEqual(normalized["bark"]["key"], "mykey")


if __name__ == "__main__":
    unittest.main()
