"""Apple Music strategy — free iTunes Search/Lookup API (no key required)."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import aiohttp

from .platform_base import LinkMatch, MusicPlatform
from .song import Song, titles_match

log = logging.getLogger(__name__)

_LOOKUP_URL = "https://itunes.apple.com/lookup"
_SEARCH_URL = "https://itunes.apple.com/search"
_TIMEOUT = aiohttp.ClientTimeout(total=15)

# /album/<slug>/<album id>?i=<track id>  or  /song/<slug>/<track id>
_MUSIC_URL = re.compile(
    r"https?://music\.apple\.com/[a-z]{2}/(?:album|song)/[^\s<>]+",
    re.IGNORECASE,
)
_SONG_PATH = re.compile(r"/song/[^/]+/(\d+)", re.IGNORECASE)


class AppleMusicError(Exception):
    pass


def _extract_track_id(url: str) -> str | None:
    """Apple Music *track* id, or None for album-only links."""
    song_match = _SONG_PATH.search(url)
    if song_match:
        return song_match.group(1)
    track_param = parse_qs(urlparse(url).query).get("i", [None])[0]
    if track_param and track_param.isdigit():
        return track_param
    return None


class AppleMusicPlatform(MusicPlatform):
    key = "appleMusic"
    label = "Apple Music"

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    def match(self, text: str) -> LinkMatch | None:
        m = _MUSIC_URL.search(text)
        if not m:
            return None
        track_id = _extract_track_id(m.group(0))
        return LinkMatch(url=m.group(0), track_id=track_id) if track_id else None

    async def _get_results(self, url: str, params: dict) -> list[dict]:
        async with self._session.get(url, params=params, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raise AppleMusicError(f"iTunes API returned HTTP {resp.status}")
            # iTunes serves JSON with a text/javascript content type.
            return json.loads(await resp.text()).get("results", [])

    async def get_song(self, track_id: str) -> Song:
        results = await self._get_results(_LOOKUP_URL, {"id": track_id, "entity": "song"})
        tracks = [r for r in results if r.get("wrapperType") == "track"]
        if not tracks:
            raise AppleMusicError(f"no track found for id {track_id}")
        track = tracks[0]
        return Song(
            title=track["trackName"],
            artist=track.get("artistName"),
            thumbnail_url=track.get("artworkUrl100"),
        )

    async def search(self, song: Song) -> str | None:
        try:
            results = await self._get_results(
                _SEARCH_URL,
                {"term": song.query, "media": "music", "entity": "song", "limit": 5},
            )
        except (AppleMusicError, aiohttp.ClientError) as exc:
            log.warning("Apple Music search failed for %r: %s", song.query, exc)
            return None
        for track in results:
            if titles_match(song.title, track.get("trackName", "")):
                return track.get("trackViewUrl")
        return None
