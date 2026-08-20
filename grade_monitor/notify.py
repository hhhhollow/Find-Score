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
    retries: int = 3,
) -> bool:
    url = f"{server.rstrip('/')}/{quote(key.strip('/'), safe='')}/"
    payload = {
        "title": title,
        "body": text,
        "group": group,
        "sound": sound,
    }

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
