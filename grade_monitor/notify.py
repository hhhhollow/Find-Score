"""Bark notification client."""

import time
from urllib.parse import quote

import requests


def send_bark(
    key: str,
    text: str,
    *,
    title: str = "Find-Score",
    server: str = "https://api.day.app",
    group: str = "Find-Score",
    sound: str = "bell",
    level: str | None = None,
    icon: str | None = None,
    retries: int = 3,
) -> bool:
    url = f"{server.rstrip('/')}/{quote(key.strip('/'), safe='')}/"
    payload = {
        "title": title,
        "body": text,
        "group": group,
        "sound": sound,
    }
    if level:
        payload["level"] = level
    if icon:
        payload["icon"] = icon

    for attempt in range(max(1, retries)):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=15,
                allow_redirects=False,
            )
            if 200 <= response.status_code < 300:
                result = response.json()
                if isinstance(result, dict) and result.get("code") == 200:
                    return True
            if 300 <= response.status_code < 500:
                return False
        except (requests.RequestException, ValueError):
            pass

        if attempt + 1 < retries:
            time.sleep(2**attempt)

    return False


def send_alert(
    key: str,
    text: str,
    *,
    title: str = "⚠️ Find-Score 异常告警",
    server: str = "https://api.day.app",
    group: str = "Find-Score",
    sound: str = "alarm",
    level: str = "timeSensitive",
    retries: int = 3,
) -> bool:
    """发送高优先级异常/风控告警通知。"""
    return send_bark(
        key,
        text,
        title=title,
        server=server,
        group=group,
        sound=sound,
        level=level,
        retries=retries,
    )

