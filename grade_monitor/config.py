"""Single-user configuration."""

import json
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit

from .storage import CONFIG_FILE

DEFAULT_INTERVAL_MINUTES = 20


class JwxtConfig(TypedDict):
    username: str
    password: str


class BarkConfig(TypedDict):
    key: str
    server: str
    group: str
    sound: str


class AppConfig(TypedDict):
    jwxt: JwxtConfig
    bark: BarkConfig
    interval_minutes: int


class ConfigError(ValueError):
    """Invalid or missing configuration."""


def _text(value: object, field: str, *, strip: bool = True) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field} 必须是非空字符串")
    result = value.strip() if strip else value
    if not result:
        raise ConfigError(f"{field} 必须是非空字符串")
    return result


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field} 必须是正整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{field} 必须是正整数") from error
    if result <= 0 or (isinstance(value, float) and not value.is_integer()):
        raise ConfigError(f"{field} 必须是正整数")
    return result


def _bark_server(value: object) -> str:
    server = _text(value, "bark.server").rstrip("/")
    parsed = urlsplit(server)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("bark.server 必须是有效的 http/https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("bark.server 不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ConfigError("bark.server 不能包含 query 或 fragment")
    return server


def load_config(path: Path = CONFIG_FILE) -> AppConfig:
    try:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
    except FileNotFoundError as error:
        raise ConfigError(f"配置文件不存在: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"配置文件不是有效 JSON: {error}") from error

    if not isinstance(raw, dict):
        raise ConfigError("配置根节点必须是对象")

    jwxt = raw.get("jwxt")
    bark = raw.get("bark")
    if not isinstance(jwxt, dict):
        raise ConfigError("jwxt 必须是对象")
    if not isinstance(bark, dict):
        raise ConfigError("bark 必须是对象")

    key = _text(bark.get("key"), "bark.key").strip("/")
    if any(character in key for character in "/?#"):
        raise ConfigError("bark.key 必须是单段 key")

    return {
        "jwxt": {
            "username": _text(jwxt.get("username"), "jwxt.username"),
            "password": _text(jwxt.get("password"), "jwxt.password", strip=False),
        },
        "bark": {
            "key": key,
            "server": _bark_server(bark.get("server", "https://api.day.app")),
            "group": _text(bark.get("group", "Find-Score"), "bark.group"),
            "sound": _text(bark.get("sound", "bell"), "bark.sound"),
        },
        "interval_minutes": _positive_int(
            raw.get("interval_minutes", DEFAULT_INTERVAL_MINUTES),
            "interval_minutes",
        ),
    }
