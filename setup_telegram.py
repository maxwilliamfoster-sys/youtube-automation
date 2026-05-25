"""
One-time setup: configure Telegram notifications for BuriedCasefiles.

Run once with:
    py setup_telegram.py

What this does:
    1. Asks for your Telegram bot token (you create the bot in 60 seconds via @BotFather)
    2. Opens the bot so you can send it a message
    3. Auto-detects your chat ID from that message
    4. Saves both values to .env
    5. Sends a test message to confirm it works
"""

import os
import time
import webbrowser
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"


def _update_env(key: str, value: str):
    """Add or update a key=value line in .env (creates the file if missing)."""
    text  = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    lines = text.splitlines()
    found, new_lines = False, []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main():
    print("\n" + "=" * 58)
    print("  BuriedCasefiles — Telegram Notification Setup")
    print("=" * 58)
    print("""
Step 1 — Create a bot (takes ~60 seconds):
  • Open Telegram and search for  @BotFather
  • Send:  /newbot
  • Choose any name (e.g. BuriedCasefiles Alerts)
  • Choose any username ending in 'bot' (e.g. bcalerts_bot)
  • BotFather replies with a token like:  7123456789:ABCdef...

Step 2 — Paste the token below.
""")

    token = input("Paste your bot token: ").strip()
    if not token or ":" not in token:
        print("\n[!] That doesn't look like a valid token (expected format: 123456:ABC...).")
        return

    # Verify token
    print("\nVerifying token with Telegram...")
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=10
        )
    except Exception as e:
        print(f"[!] Could not reach Telegram: {e}")
        return

    if not resp.ok:
        print(f"[!] Telegram rejected the token: {resp.text}")
        return

    bot_username = resp.json()["result"]["username"]
    print(f"[OK] Bot verified: @{bot_username}")

    # Open the bot so the user can start it
    bot_url = f"https://t.me/{bot_username}"
    print(f"\nOpening your bot: {bot_url}")
    print("→ Press START (or send any message) in Telegram, then come back here.")
    webbrowser.open(bot_url)
    input("\nPress Enter once you've sent a message to the bot...")

    # Poll getUpdates to get the chat_id
    chat_id = None
    print("Detecting your chat ID...")
    for attempt in range(12):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                timeout=10,
            )
            updates = r.json().get("result", [])
            if updates:
                chat_id = updates[-1]["message"]["chat"]["id"]
                break
        except Exception:
            pass
        time.sleep(2)

    if not chat_id:
        print(
            "\n[!] No message detected. Make sure you sent a message to the bot, "
            "then run this script again."
        )
        return

    print(f"[OK] Chat ID: {chat_id}")

    # Save to .env
    _update_env("TELEGRAM_BOT_TOKEN", token)
    _update_env("TELEGRAM_CHAT_ID", str(chat_id))
    print("[OK] Saved to .env")

    # Send a test message
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "✅ <b>BuriedCasefiles notifications active!</b>\n\n"
                    "You'll get a message here if TikTok upload fails "
                    "(e.g. cookies expired and need refreshing)."
                ),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if r.ok:
            print("[OK] Test message sent — check your Telegram!")
        else:
            print(f"[!] Test message failed: {r.text}")
    except Exception as e:
        print(f"[!] Could not send test message: {e}")

    print("\n" + "=" * 58)
    print("  Setup complete. You'll be notified on your phone")
    print("  whenever the automation needs your attention.")
    print("=" * 58 + "\n")


if __name__ == "__main__":
    main()
