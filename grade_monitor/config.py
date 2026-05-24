"""
配置文件加载 & 多用户兼容。
"""

import json

from .constants import CONFIG_FILE
from .logging_config import log


def load_config() -> dict:
    """读取 config.json 并返回完整配置字典。"""
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_users(cfg: dict) -> list[dict]:
    """支持新 multi-user 格式（cfg.users[]）和旧 single-user 格式。

    每个 user dict 必须含 name / jwxt / telegram。
    """
    if "users" in cfg:
        users = cfg["users"]
    else:
        # 旧版兼容：顶层 jwxt + telegram → 单用户
        users = [{
            "name": cfg["jwxt"]["username"],
            "jwxt": cfg["jwxt"],
            "telegram": cfg["telegram"],
        }]
    for u in users:
        u.setdefault("name", u["jwxt"]["username"])
    return users
