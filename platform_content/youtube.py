"""YouTube URL helpers for platform content embeds."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_youtube_video_id(url: str | None) -> str | None:
    """Return the 11-char YouTube video id, or None if not a valid YouTube URL."""
    if not url or not str(url).strip():
        return None
    raw = str(url).strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"

    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None

    path = parsed.path or ""
    query = parse_qs(parsed.query or "")

    if host in {"youtu.be", "www.youtu.be"}:
        candidate = path.strip("/").split("/")[0] if path.strip("/") else ""
    elif "/embed/" in path or "/shorts/" in path or "/live/" in path or "/v/" in path:
        parts = [p for p in path.split("/") if p]
        candidate = parts[-1] if parts else ""
    else:
        candidate = (query.get("v") or [None])[0] or ""

    if candidate and _VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def youtube_embed_url(url: str | None) -> str | None:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return None
    return f"https://www.youtube.com/embed/{video_id}"
