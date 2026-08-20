import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import grade_monitor.__main__ as app
from grade_monitor.config import ConfigError, load_config
from grade_monitor.session import CAS_HOST, JwxtSession


class RegressionTests(unittest.TestCase):
    def test_merge_snapshot_preserves_temporarily_missing_courses(self) -> None:
        old = {"2025-2026-2|A": "90", "2025-2026-2|B": "80"}
        current = {"2025-2026-2|A": "91"}

        self.assertEqual(
            app._merge_snapshot(old, current),
            {"2025-2026-2|A": "91", "2025-2026-2|B": "80"},
        )

    def test_app_context_network_failure_is_not_success(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()
        client.session.get.side_effect = requests.RequestException("network down")

        self.assertFalse(client._register_app_context())

    def test_app_context_redirect_to_cas_is_not_success(self) -> None:
        client = object.__new__(JwxtSession)
        client.session = Mock()
        response = Mock(url=f"{CAS_HOST}/authserver/login")
        client.session.get.return_value = response

        self.assertFalse(client._register_app_context())

    def test_slash_only_bark_key_is_rejected(self) -> None:
        raw = {
            "jwxt": {"username": "2024012345", "password": "secret"},
            "bark": {"key": "///"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "bark.key"):
                load_config(path)

    def test_logging_initialization_error_returns_failure(self) -> None:
        with patch.object(app, "configure_logging", side_effect=OSError("read-only")):
            self.assertEqual(app.main(), 1)


if __name__ == "__main__":
    unittest.main()
