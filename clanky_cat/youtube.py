"""YouTube strategy — ytmusicapi (unauthenticated, no API key required).

ytmusicapi is synchronous, so calls are pushed to a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import parse_qs, urlparse

from ytmusicapi import YTMusic

from .platform_base import LinkMatch, MusicPlatform
from .song import Song, titles_match

log = logging.getLogger(__name__)

_WATCH_URL = re.compile(
    r"https?://(?:music\.youtube\.com/watch\?\S+"
    r"|(?:www\.|m\.)?youtube\.com/watch\?\S+"
    r"|youtu\.be/[A-Za-z0-9_-]{6,}(?:\?\S*)?)",
    re.IGNORECASE,
)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")


class YouTubeError(Exception):
    pass


def _extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("youtu.be"):
        video_id = parsed.path.lstrip("/")
    else:
        video_id = parse_qs(parsed.query).get("v", [None])[0]
    if video_id and _VIDEO_ID.match(video_id):
        return video_id
    return None


def _strip_topic(author: str) -> str:
    return author.removesuffix(" - Topic").strip()


class YouTubePlatform(MusicPlatform):
    key = "youtube"
    label = "YouTube"

    def __init__(self) -> None:
        self._client: YTMusic | None = None

    def _ytmusic(self) -> YTMusic:
        if self._client is None:
            self._client = YTMusic()
        return self._client

    def match(self, text: str) -> LinkMatch | None:
        m = _WATCH_URL.search(text)
        if not m:
            return None
        video_id = _extract_video_id(m.group(0))
        return LinkMatch(url=m.group(0), track_id=video_id) if video_id else None

    def _get_song_sync(self, video_id: str) -> Song:
        details = self._ytmusic().get_song(video_id).get("videoDetails") or {}
        title = details.get("title")
        if not title:
            raise YouTubeError(f"no video details for {video_id}")
        thumbnails = (details.get("thumbnail") or {}).get("thumbnails", [])
        return Song(
            title=title,
            artist=_strip_topic(details.get("author", "")) or None,
            thumbnail_url=thumbnails[-1]["url"] if thumbnails else None,
        )

    def _search_sync(self, song: Song) -> str | None:
        results = self._ytmusic().search(song.query, filter="songs", limit=5)
        for item in results:
            if item.get("videoId") and titles_match(song.title, item.get("title", "")):
                return f"https://www.youtube.com/watch?v={item['videoId']}"
        return None

    async def get_song(self, track_id: str) -> Song:
        return await asyncio.to_thread(self._get_song_sync, track_id)

    async def search(self, song: Song) -> str | None:
        try:
            return await asyncio.to_thread(self._search_sync, song)
        except Exception as exc:
            log.warning("YouTube search failed for %r: %s", song.query, exc)
            return None
