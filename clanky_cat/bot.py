"""Clanky Cat — Discord bot that swaps music links for a cross-platform embed.

Watches one channel. When a message contains a song link recognized by any
registered platform, the original message is deleted and replaced with an
embed listing the song on every platform (NOT FOUND where no match).

The bot only knows the MusicPlatform interface; everything platform-specific
lives in the strategy classes registered in setup_hook.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp
import discord
from dotenv import load_dotenv

from .apple import AppleMusicPlatform
from .platform_base import LinkMatch, MusicPlatform
from .song import Song
from .spotify import SpotifyPlatform
from .youtube import YouTubePlatform

log = logging.getLogger("clanky_cat")

EMBED_COLOR = 0xE91E63
NOT_FOUND = "NOT FOUND"


class ClankyCat(discord.Client):
    def __init__(self, channel_id: int, spotify_id: str | None, spotify_secret: str | None) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # required to read message text
        super().__init__(intents=intents)
        self.channel_id = channel_id
        self._spotify_id = spotify_id
        self._spotify_secret = spotify_secret
        self.http_session: aiohttp.ClientSession | None = None
        self.platforms: list[MusicPlatform] = []

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        self.platforms = [
            SpotifyPlatform(self.http_session, self._spotify_id, self._spotify_secret),
            AppleMusicPlatform(self.http_session),
            YouTubePlatform(),
        ]
        for platform in self.platforms:
            if not platform.enabled:
                log.warning(
                    "%s is disabled (missing credentials): its links are ignored "
                    "and it will always show NOT FOUND",
                    platform.label,
                )

    async def close(self) -> None:
        if self.http_session is not None:
            await self.http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s, watching channel %s", self.user, self.channel_id)

    def _match_platform(self, text: str) -> tuple[MusicPlatform, LinkMatch] | None:
        for platform in self.platforms:
            link = platform.match(text)
            if link:
                return platform, link
        return None

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.channel.id != self.channel_id:
            return

        matched = self._match_platform(message.content)
        if matched is None:
            return
        source, link = matched
        if not source.enabled:
            return  # can't resolve the source song

        log.info("%s link from %s: %s", source.key, message.author, link.url)

        try:
            song = await source.get_song(link.track_id)
        except Exception:
            log.exception("Could not resolve %s, leaving message untouched", link.url)
            return

        others = [p for p in self.platforms if p is not source]
        results = await asyncio.gather(*(p.search(song) for p in others))
        links = {p.key: url for p, url in zip(others, results)}
        links[source.key] = link.url

        # Post the replacement first so the song is never lost if delete fails.
        await message.channel.send(embed=self._build_embed(message, song, links))
        try:
            await message.delete()
        except discord.Forbidden:
            log.warning("Missing Manage Messages permission; could not delete original")
        except discord.NotFound:
            pass  # already deleted

    def _build_embed(
        self, message: discord.Message, song: Song, links: dict[str, str | None]
    ) -> discord.Embed:
        title = f"{song.title} — {song.artist}" if song.artist else song.title
        embed = discord.Embed(title=title, color=EMBED_COLOR)
        if song.thumbnail_url:
            embed.set_thumbnail(url=song.thumbnail_url)

        for platform in self.platforms:
            url = links.get(platform.key)
            value = f"[Listen]({url})" if url else NOT_FOUND
            embed.add_field(name=platform.label, value=value, inline=True)

        embed.set_footer(
            text=f"Shared by {message.author.display_name}",
            icon_url=message.author.display_avatar.url,
        )
        return embed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    channel_id = os.getenv("CHANNEL_ID")
    if not token or not channel_id:
        raise SystemExit("Set DISCORD_TOKEN and CHANNEL_ID in .env (see .env.example)")

    bot = ClankyCat(
        channel_id=int(channel_id),
        spotify_id=os.getenv("SPOTIFY_CLIENT_ID"),
        spotify_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    )
    bot.run(token)


if __name__ == "__main__":
    main()
