"""Strategy interface every music platform implements.

A platform owns everything specific to itself: the regexes that recognize
its links, the API client that resolves metadata, and the search that maps
a Song back to a URL. The bot only ever talks to this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .song import Song


@dataclass(frozen=True)
class LinkMatch:
    """A recognized song link: the URL as posted plus the platform's track id."""

    url: str
    track_id: str


class MusicPlatform(ABC):
    key: str  # stable identifier, e.g. "spotify"
    label: str  # display name for embeds, e.g. "Spotify"
    enabled: bool = True  # False = platform can't resolve (e.g. missing creds)

    @abstractmethod
    def match(self, text: str) -> LinkMatch | None:
        """Return the first link in *text* this platform recognizes, or None.

        Links that don't identify a single track (albums, playlists) must
        return None.
        """

    @abstractmethod
    async def get_song(self, track_id: str) -> Song:
        """Metadata for one of this platform's track ids. Raises on failure."""

    @abstractmethod
    async def search(self, song: Song) -> str | None:
        """URL of the best match for *song* on this platform, or None.

        Must not raise; resolution failures are logged and become None
        (rendered as NOT FOUND).
        """
