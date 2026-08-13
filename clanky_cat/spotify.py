"""Spotify strategy — Web API with the client-credentials flow.

Needs a free Spotify app (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET from
https://developer.spotify.com/dashboard). Without credentials the platform
is disabled: source links are ignored and searches return None (NOT FOUND).
"""

from __future__ import annotations

import base64
import logging
import re
import time

import aiohttp

from .platform_base import LinkMatch, MusicPlatform
from .song import Song, titles_match

log = logging.getLogger(__name__)

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API = "https://api.spotify.com/v1"
_TIMEOUT = aiohttp.ClientTimeout(total=15)

_TRACK_URL = re.compile(
    r"https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]+)(?:\?\S*)?",
    re.IGNORECASE,
)


class SpotifyError(Exception):
    pass


class SpotifyPlatform(MusicPlatform):
    key = "spotify"
    label = "Spotify"

    def __init__(self, session: aiohttp.ClientSession, client_id: str | None, client_secret: str | None) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.enabled = bool(client_id and client_secret)

    def match(self, text: str) -> LinkMatch | None:
        m = _TRACK_URL.search(text)
        return LinkMatch(url=m.group(0), track_id=m.group(1)) if m else None

    async def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        async with self._session.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise SpotifyError(f"token request failed: HTTP {resp.status}")
            data = await resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 60
        return self._token

    async def _get(self, path: str, params: dict) -> dict:
        token = await self._get_token()
        async with self._session.get(
            f"{_API}{path}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise SpotifyError(f"GET {path} failed: HTTP {resp.status}")
            return await resp.json()

    async def get_song(self, track_id: str) -> Song:
        if not self.enabled:
            raise SpotifyError("Spotify credentials not configured")
        data = await self._get(f"/tracks/{track_id}", {})
        images = data.get("album", {}).get("images", [])
        return Song(
            title=data["name"],
            artist=", ".join(a["name"] for a in data.get("artists", [])) or None,
            thumbnail_url=images[0]["url"] if images else None,
        )

    async def search(self, song: Song) -> str | None:
        if not self.enabled:
            return None
        try:
            data = await self._get("/search", {"q": song.query, "type": "track", "limit": 5})
        except (SpotifyError, aiohttp.ClientError) as exc:
            log.warning("Spotify search failed for %r: %s", song.query, exc)
            return None
        for item in data.get("tracks", {}).get("items", []):
            if titles_match(song.title, item["name"]):
                return item["external_urls"].get("spotify")
        return None
