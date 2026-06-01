"""
Single source of truth for the YouTube OAuth scopes used across the project.

Keeping this in one place ensures the uploader, the performance tracker, and the
re-auth script all agree on what the token.json is expected to contain (a scope
mismatch makes google-auth refuse to load the token).
"""

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",        # post videos
    "https://www.googleapis.com/auth/youtube.force-ssl",     # read + manage (set privacy)
    "https://www.googleapis.com/auth/yt-analytics.readonly", # retention / view-duration metrics
]
