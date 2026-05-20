"""
YouTube Uploader — uploads finished Shorts to your YouTube channel.
Uses OAuth 2.0 (one-time browser login, then token is saved for future uploads).
Requires: client_secrets.json from Google Cloud Console (see SETUP.md).
"""

import os
import json
import pickle
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from config import (
    YOUTUBE_CATEGORY_ID, YOUTUBE_PRIVACY, YOUTUBE_TAGS,
    YOUTUBE_DESCRIPTION_TEMPLATE, CREDENTIALS_FILE, TOKEN_FILE
)

# YouTube API scopes needed
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_authenticated_service():
    """
    Get an authenticated YouTube API service.
    On first run: opens browser for OAuth login.
    Subsequent runs: uses saved token automatically.
    """
    creds = None

    # Load saved token if it exists
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # If no valid credentials, authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[YouTube] Refreshing access token...")
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"\n[YouTube] Missing: {CREDENTIALS_FILE}\n"
                    "Please follow the YouTube API setup in SETUP.md to create this file.\n"
                    "It's a one-time setup — takes about 5 minutes."
                )

            print("[YouTube] Opening browser for one-time login...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080, open_browser=True)

        # Save token for next time
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("[YouTube] Token saved — won't need to log in again!")

    return build("youtube", "v3", credentials=creds)


def upload_short(
    video_path: str,
    title: str,
    description: str = None,
    tags: list = None,
    privacy: str = None,
    story_hashtags: str = "",
) -> dict:
    """
    Upload a video as a YouTube Short.

    Args:
        video_path:   Path to the .mp4 file
        title:        Video title
        description:  Video description (uses template if None)
        tags:         List of tags (uses config defaults if None)
        privacy:      "public", "private", or "unlisted"

    Returns:
        dict with 'video_id', 'url', 'title'
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    # Use defaults from config if not specified
    if description is None:
        description = YOUTUBE_DESCRIPTION_TEMPLATE.format(
            title=title,
            story_hashtags=story_hashtags,
        )
    if tags is None:
        tags = YOUTUBE_TAGS
    if privacy is None:
        privacy = YOUTUBE_PRIVACY

    # YouTube Shorts requirement: title must end with #Shorts or be in description
    if "#Shorts" not in title and "#Shorts" not in description:
        description = "#Shorts\n\n" + description

    youtube = get_authenticated_service()

    print(f"[YouTube] Uploading: {title}")
    print(f"[YouTube] Privacy: {privacy}")

    body = {
        "snippet": {
            "title": title[:100],           # YouTube max title length
            "description": description[:5000],
            "tags": tags[:500],             # YouTube max tags
            "categoryId": YOUTUBE_CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        }
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024  # 1MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    # Upload with progress tracking
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            print(f"[YouTube] Upload progress: {progress}%", end="\r")

    video_id = response["id"]
    url = f"https://www.youtube.com/shorts/{video_id}"

    print(f"\n[YouTube] Upload complete!")
    print(f"[YouTube] URL: {url}")

    return {
        "video_id": video_id,
        "url": url,
        "title": title,
    }


if __name__ == "__main__":
    print("YouTube uploader — requires client_secrets.json setup.")
    print("See SETUP.md for instructions.")
