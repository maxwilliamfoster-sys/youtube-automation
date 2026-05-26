"""
Telegram notifier for the YouTube Shorts automation pipeline.
Sends messages to your Telegram chat for upload results and credential alerts.

Credentials read from .env:
  TELEGRAM_BOT_TOKEN=<your bot token>
  TELEGRAM_CHAT_ID=<your chat id>
"""

import os
import urllib.request
import urllib.error
import json
from dotenv import load_dotenv

load_dotenv()

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_telegram(message: str) -> bool:
    """
    Send a Telegram message to the configured chat.
    Returns True on success. Silent — never raises or crashes the pipeline.
    """
    if not _TOKEN or not _CHAT_ID:
        return False
    try:
        payload = json.dumps({
            "chat_id":    _CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[Notify] Telegram alert failed — {e}")
        return False


# Convenience aliases used throughout the pipeline
def notify_success(title: str, url: str) -> None:
    send_telegram(
        f"✅ <b>New Short Posted!</b>\n\n"
        f"<b>{title}</b>\n"
        f"{url}"
    )


def notify_failure(reason: str) -> None:
    send_telegram(
        f"❌ <b>Pipeline Failed</b>\n\n"
        f"{reason}\n\n"
        f"Check GitHub Actions for the full log."
    )


def notify_credential_expiry(service: str, steps: str) -> None:
    send_telegram(
        f"🔑 <b>Action Required — {service} credentials expired</b>\n\n"
        f"{steps}"
    )
