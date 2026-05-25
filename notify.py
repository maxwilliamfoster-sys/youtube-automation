"""
Telegram alert system for BuriedCasefiles automation.
Sends a message to your phone when TikTok upload fails.

Setup: run  py setup_telegram.py  once to configure.
"""

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
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False
