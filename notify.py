"""
Telegram alert + delivery for BuriedCasefiles automation.
Sends text alerts and finished videos to your phone.

Setup: run  py setup_telegram.py  once to configure.
"""

import html
import os
import requests
from dotenv import load_dotenv

load_dotenv()

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_alert(message: str) -> bool:
    """
    Send a Telegram message to the configured chat.
    Returns True on success, False if not configured or request fails.
    Silent — never raises.
    """
    if not _TOKEN or not _CHAT_ID:
        return False  # Not configured — skip silently
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        return resp.ok
    except Exception:
        return False


def send_video(video_path: str, caption: str = "") -> bool:
    """
    Send a finished video file to the configured chat (max 50 MB via the bot API).
    `caption` may contain HTML. Returns True on success. Silent — never raises.
    """
    if not _TOKEN or not _CHAT_ID:
        return False
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{_TOKEN}/sendVideo",
                data={
                    "chat_id": _CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                    "supports_streaming": "true",
                },
                files={"video": f},
                timeout=300,
            )
        return resp.ok
    except Exception as e:
        print(f"[Notify] send_video failed: {e}")
        return False


def esc(text: str) -> str:
    """HTML-escape dynamic text so it's safe inside a parse_mode=HTML message."""
    return html.escape(str(text or ""))
