"""
消息推送：Telegram（带重试 & 分批发送）+ macOS 本地通知。
"""

import subprocess
import time

import requests

from .logging_config import log


def send_telegram(bot_token: str, chat_id: str, text: str,
                  retries: int = 3) -> bool:
    """发送一条 Telegram 消息，失败自动重试。"""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            return True
        except Exception as e:
            wait = 2 ** attempt
            log.warning(
                f"Telegram 推送失败 (尝试 {attempt + 1}/{retries}): {e}，"
                f"{wait}s 后重试"
            )
            time.sleep(wait)
    log.error("Telegram 推送最终失败")
    return False


def send_macos_notification(title: str, message: str,
                            subtitle: str = "", sound: str = "Glass") -> None:
    """通过 osascript 发送 macOS 系统通知。失败静默，不影响主流程。"""
    # 转义双引号和反斜杠，防止 AppleScript 注入
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{_esc(message)}" with title "{_esc(title)}"'
    if subtitle:
        script += f' subtitle "{_esc(subtitle)}"'
    if sound:
        script += f' sound name "{_esc(sound)}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            timeout=5, capture_output=True,
        )
    except Exception as e:
        log.debug(f"macOS 通知发送失败（不影响 Telegram）: {e}")


def send_batch(bot_token: str, chat_id: str, header: str,
               bodies: list[str]) -> None:
    """将多条成绩消息拼接发送，单条超 4000 字符时自动拆分。"""
    msg = header
    for body in bodies:
        if len(msg) + len(body) + 2 > 4000:
            send_telegram(bot_token, chat_id, msg)
            msg = ""
        msg += body + "\n\n"
    if msg.strip():
        send_telegram(bot_token, chat_id, msg)
