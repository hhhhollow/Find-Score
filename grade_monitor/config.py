"""配置加载与边界校验，兼容旧版单用户格式。"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .constants import CONFIG_FILE
from .storage import safe_name

DEFAULT_INTERVAL_MINUTES = 20


class ConfigError(ValueError):
    """配置文件缺失、字段错误或用户身份冲突。"""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"配置字段 {field} 必须是对象")
    return value


def _text(value: Any, field: str, *, strip: bool = True) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"配置字段 {field} 必须是非空字符串")
    normalized = value.strip() if strip else value
    if not normalized:
        raise ConfigError(f"配置字段 {field} 必须是非空字符串")
    return normalized


def _chat_id(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise ConfigError(f"配置字段 {field} 必须是字符串或整数")
    if isinstance(value, int):
        return str(value)
    return _text(value, field)


def _bark_server(value: Any, field: str) -> str:
    """校验 Bark 服务地址，禁止非 HTTP(S)、凭据、查询参数和 fragment。"""
    server = _text(value, field).rstrip("/")
    parsed = urlsplit(server)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"配置字段 {field} 必须是有效的 http/https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"配置字段 {field} 不能在 URL 中包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"配置字段 {field} 不能包含 query 或 fragment")
    return server


def _bark_key(value: Any, field: str) -> str:
    key = _text(value, field).strip("/")
    if not key or any(character in key for character in "/?#"):
        raise ConfigError(f"配置字段 {field} 必须是单段 Bark key")
    return key


def _bark_from_url(value: str, field: str) -> dict[str, str]:
    """把 https://host[/prefix]/key 拆成 server + key，并保持可选路径前缀。"""
    raw = _text(value, field).rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"配置字段 {field} 必须是有效的 http/https Bark URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"配置字段 {field} 不能在 URL 中包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"配置字段 {field} 不能包含 query 或 fragment")

    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ConfigError(f"配置字段 {field} 的 URL 必须包含 Bark key")

    key = _bark_key(path_parts[-1], f"{field}.key")
    prefix = "/" + "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
    server = urlunsplit((parsed.scheme, parsed.netloc, prefix, "", "")).rstrip("/")
    return {"key": key, "server": server}


def _normalize_user(raw: Any, index: int) -> dict:
    prefix = f"users[{index}]"
    user = _mapping(raw, prefix)
    jwxt = _mapping(user.get("jwxt"), f"{prefix}.jwxt")

    has_telegram = "telegram" in user and user["telegram"] is not None
    has_bark = "bark" in user and user["bark"] is not None

    if not has_telegram and not has_bark:
        raise ConfigError(f"配置字段 {prefix} 必须包含 telegram 或 bark 通知配置")

    telegram_dict = None
    if has_telegram:
        telegram = _mapping(user.get("telegram"), f"{prefix}.telegram")
        telegram_dict = {
            "bot_token": _text(
                telegram.get("bot_token"), f"{prefix}.telegram.bot_token",
            ),
            "chat_id": _chat_id(
                telegram.get("chat_id"), f"{prefix}.telegram.chat_id",
            ),
        }

    bark_dict = None
    if has_bark:
        bark_val = user.get("bark")
        if isinstance(bark_val, str):
            bark_dict = _bark_from_url(bark_val, f"{prefix}.bark")
        elif isinstance(bark_val, Mapping):
            key = _bark_key(bark_val.get("key"), f"{prefix}.bark.key")
            server = _bark_server(
                bark_val.get("server", "https://api.day.app"),
                f"{prefix}.bark.server",
            )
            bark_dict = {
                "key": key,
                "server": server,
                "sound": _text(
                    bark_val.get("sound", "bell"), f"{prefix}.bark.sound",
                ),
                "group": _text(
                    bark_val.get("group", "Find-Score"), f"{prefix}.bark.group",
                ),
            }
        else:
            raise ConfigError(f"配置字段 {prefix}.bark 必须是字符串或对象")

    username = _text(jwxt.get("username"), f"{prefix}.jwxt.username")
    name_value = user.get("name", username)
    name = _text(name_value, f"{prefix}.name")
    if len(name) > 64:
        raise ConfigError(f"配置字段 {prefix}.name 不能超过 64 个字符")

    normalized_user: dict[str, Any] = {
        "name": name,
        "jwxt": {
            "username": username,
            # 密码的前后空格可能是有效字符，不做 strip。
            "password": _text(
                jwxt.get("password"), f"{prefix}.jwxt.password", strip=False,
            ),
        },
    }
    if telegram_dict is not None:
        normalized_user["telegram"] = telegram_dict
    if bark_dict is not None:
        normalized_user["bark"] = bark_dict

    return normalized_user


def load_users(cfg: Mapping[str, Any]) -> list[dict]:
    """返回经过复制和校验的用户列表，不修改传入配置。"""
    if "users" in cfg:
        raw_users = cfg["users"]
        if not isinstance(raw_users, list) or not raw_users:
            raise ConfigError("配置字段 users 必须是非空数组")
    else:
        # 旧版：顶层 jwxt + telegram/bark。
        raw_users = [{
            "jwxt": cfg.get("jwxt"),
            "telegram": cfg.get("telegram"),
            "bark": cfg.get("bark"),
        }]

    users = [_normalize_user(raw, index) for index, raw in enumerate(raw_users)]

    display_names: set[str] = set()
    storage_names: set[str] = set()
    for user in users:
        display_key = user["name"].casefold()
        storage_key = safe_name(user["name"]).casefold()
        if display_key in display_names:
            raise ConfigError(f"用户 name 重复: {user['name']}")
        if storage_key in storage_names:
            raise ConfigError(
                f"用户 name 映射到同一缓存文件: {user['name']} "
                f"({safe_name(user['name'])})",
            )
        display_names.add(display_key)
        storage_names.add(storage_key)

    return users


def _normalize_interval(value: Any) -> int:
    if isinstance(value, bool):
        raise ConfigError("interval_minutes 必须是正整数")
    if isinstance(value, float) and not value.is_integer():
        raise ConfigError("interval_minutes 必须是正整数")
    try:
        interval = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigError("interval_minutes 必须是正整数") from error
    if interval <= 0:
        raise ConfigError("interval_minutes 必须大于 0")
    return interval


def load_config(path: Path = CONFIG_FILE) -> dict:
    """读取、规范化并校验配置；返回新的字典。"""
    try:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
    except FileNotFoundError as error:
        raise ConfigError(f"配置文件不存在: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"配置文件不是有效 JSON: {error}") from error

    cfg = dict(_mapping(raw, "根节点"))
    cfg["interval_minutes"] = _normalize_interval(
        cfg.get("interval_minutes", DEFAULT_INTERVAL_MINUTES),
    )
    cfg["users"] = load_users(cfg)
    return cfg
