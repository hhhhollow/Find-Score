"""Telegram 与跨平台桌面通知适配器。"""

import platform
import shutil
import subprocess
import time

import requests

from .logging_config import log

TELEGRAM_MESSAGE_LIMIT = 4000


def _retry_after(response: requests.Response, default: int) -> int:
    try:
        value = response.json().get("parameters", {}).get("retry_after")
        return max(1, int(value))
    except (AttributeError, TypeError, ValueError):
        return default


def send_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
    retries: int = 3,
) -> bool:
    """发送 Telegram 消息；仅重试限流、服务端和网络错误。"""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    for attempt in range(max(0, retries)):
        wait = 2**attempt
        try:
            response = requests.post(url, json=payload, timeout=15)
            if 200 <= response.status_code < 300:
                try:
                    telegram_result = response.json()
                except Exception:
                    telegram_result = None
                if (
                    isinstance(telegram_result, dict)
                    and telegram_result.get("ok") is True
                ):
                    return True
                detail = f"HTTP {response.status_code} 但响应未确认送达"
            elif 400 <= response.status_code < 500 and response.status_code != 429:
                log.error(f"Telegram 推送被拒绝 (HTTP {response.status_code})")
                return False
            else:
                if response.status_code == 429:
                    wait = _retry_after(response, wait)
                detail = f"HTTP {response.status_code}"
        except Exception as error:
            # 不记录异常原文，避免 requests URL 中的 bot token 泄漏到日志。
            detail = type(error).__name__

        if attempt + 1 < retries:
            log.warning(
                f"Telegram 推送失败 (尝试 {attempt + 1}/{retries}, {detail})，"
                f"{wait}s 后重试",
            )
            time.sleep(wait)

    log.error("Telegram 推送最终失败")
    return False


def build_local_notification_command(
    system: str,
    title: str,
    message: str,
    subtitle: str = "",
    sound: str = "Glass",
) -> list[str] | None:
    """生成不经 shell 的平台本地通知命令。"""

    def escaped(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    if system == "Darwin":
        script = (
            f'display notification "{escaped(message)}" '
            f'with title "{escaped(title)}"'
        )
        if subtitle:
            script += f' subtitle "{escaped(subtitle)}"'
        if sound:
            script += f' sound name "{escaped(sound)}"'
        return ["osascript", "-e", script]

    if system == "Linux":
        display_title = title if not subtitle else f"{title} — {subtitle}"
        return [
            "notify-send",
            "--app-name",
            "Find Score",
            "--",
            display_title,
            message,
        ]
    return None


def send_local_notification(
    title: str,
    message: str,
    subtitle: str = "",
    sound: str = "Glass",
    *,
    system: str | None = None,
) -> bool:
    """发送 macOS/Linux 桌面通知；失败不影响 Telegram。"""
    current_system = platform.system() if system is None else system
    command = build_local_notification_command(
        current_system, title, message, subtitle, sound,
    )
    if command is None:
        log.debug(f"当前平台 {current_system} 不支持桌面通知")
        return False
    if shutil.which(command[0]) is None:
        log.debug(f"未找到桌面通知命令: {command[0]}")
        return False
    try:
        result = subprocess.run(
            command,
            timeout=5,
            capture_output=True,
        )
    except Exception as error:
        log.debug(f"桌面通知发送失败: {type(error).__name__}")
        return False
    if result.returncode != 0:
        log.debug(f"桌面通知命令退出码: {result.returncode}")
        return False
    return True


def send_macos_notification(
    title: str,
    message: str,
    subtitle: str = "",
    sound: str = "Glass",
) -> bool:
    """兼容旧调用；新代码应使用 send_local_notification。"""
    return send_local_notification(
        title, message, subtitle, sound, system="Darwin",
    )


def build_messages(
    header: str,
    bodies: list[str],
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> list[str]:
    """按完整 HTML 块分批，不在标签或实体中间截断。"""
    if not bodies:
        return []
    if len(header) > limit:
        raise ValueError("Telegram 消息头超过长度限制")

    messages: list[str] = []
    current = header
    for body in bodies:
        block = f"{body}\n\n"
        if len(block.rstrip()) > limit:
            raise ValueError("Telegram 单个消息块超过长度限制")
        if len(current) + len(block) > limit:
            if current.strip():
                messages.append(current.rstrip())
            current = block
        else:
            current += block
    if current.strip():
        messages.append(current.rstrip())
    return messages


def send_bark(
    key: str,
    text: str,
    title: str = "Find-Score",
    server: str = "https://api.day.app",
    group: str = "Find-Score",
    sound: str = "bell",
    retries: int = 3,
) -> bool:
    """发送 Bark (iOS) 通知；仅重试服务端和网络错误。"""
    import html
    import re

    clean_server = server.rstrip("/")
    clean_key = key.strip("/")
    url = f"{clean_server}/{clean_key}/"
    plain_text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    plain_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()

    payload = {
        "title": plain_title,
        "body": plain_text,
        "group": group,
        "sound": sound,
    }

    for attempt in range(max(0, retries)):
        wait = 2**attempt
        try:
            response = requests.post(url, json=payload, timeout=15)
            if 200 <= response.status_code < 300:
                try:
                    result = response.json()
                except Exception:
                    result = None
                if isinstance(result, dict) and result.get("code") == 200:
                    return True
                detail = f"HTTP {response.status_code} 响应未包含 code=200"
            elif 400 <= response.status_code < 500:
                log.error(f"Bark 推送被拒绝 (HTTP {response.status_code})")
                return False
            else:
                detail = f"HTTP {response.status_code}"
        except Exception as error:
            detail = type(error).__name__

        if attempt + 1 < retries:
            log.warning(
                f"Bark 推送失败 (尝试 {attempt + 1}/{retries}, {detail})，"
                f"{wait}s 后重试",
            )
            time.sleep(wait)

    log.error("Bark 推送最终失败")
    return False


def send_notification_channels(
    channels: dict,
    text: str,
    title: str = "Find-Score",
) -> bool:
    """分发通知到所有已配置的远端通知渠道（Bark、Telegram 等）。
    只要至少一个有效配置的渠道发送成功，即返回 True。
    """
    if not channels:
        return False

    delivered_any = False

    bark_cfg = channels.get("bark")
    if isinstance(bark_cfg, dict) and bark_cfg.get("key"):
        if send_bark(
            key=bark_cfg["key"],
            text=text,
            title=title,
            server=bark_cfg.get("server", "https://api.day.app"),
            group=bark_cfg.get("group", "Find-Score"),
            sound=bark_cfg.get("sound", "bell"),
        ):
            delivered_any = True

    telegram_cfg = channels.get("telegram")
    if isinstance(telegram_cfg, dict) and telegram_cfg.get("bot_token") and telegram_cfg.get("chat_id"):
        if send_telegram(
            bot_token=telegram_cfg["bot_token"],
            chat_id=telegram_cfg["chat_id"],
            text=text,
        ):
            delivered_any = True

    return delivered_any


def send_batch(
    bot_token: str,
    chat_id: str,
    header: str,
    bodies: list[str],
) -> bool:
    """批量发送消息，并把投递结果返回给状态提交方。"""
    try:
        messages = build_messages(header, bodies)
    except ValueError as error:
        log.error(str(error))
        return False
    for message in messages:
        if not send_telegram(bot_token, chat_id, message):
            return False
    return True

