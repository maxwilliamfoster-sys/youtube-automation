"""
Notifier — sends push notifications via ntfy.sh.
Used to alert you when credentials need refreshing.
"""

import urllib.request
import urllib.error
import os


def send_ntfy(title: str, message: str, priority: str = "high", tags: str = "warning") -> None:
    """
    Send a push notification via ntfy.sh.
    Requires NTFY_TOPIC to be set in .env or environment.
    Silently no-ops if NTFY_TOPIC is not configured.
    """
    topic = os.getenv("NTFY_TOPIC", "")
    if not topic:
        return

    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title":    title,
                "Priority": priority,
                "Tags":     tags,
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        # Never let a notification failure crash the pipeline
        print(f"[Notify] Warning: could not send ntfy alert — {e}")
