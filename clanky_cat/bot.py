"""Clanky Cat — Discord bot that adds a cross-platform embed to music links.

Watches one channel. When a message contains a song link recognized by any
registered platform, the bot replies to it with an embed listing the song on
every platform (NOT FOUND where no match). The reply is seeded with 👍/👎
reactions so users can give feedback on the match; a 👎 files a GitHub issue
(one per run, deduped by the run id shown in the embed footer).

TikTok and Instagram Reel links are handled separately: the video is
downloaded as an mp4 and replied to the message as an attachment.

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
from .issues import GitHubError, IssueReporter, Run, make_run_id
from .platform_base import LinkMatch, MusicPlatform
from .song import Song
from .spotify import SpotifyPlatform
from .video import VideoDownloader, VideoError
from .youtube import YouTubePlatform

log = logging.getLogger("clanky_cat")

EMBED_COLOR = 0xE91E63
NOT_FOUND = "NOT FOUND"
FEEDBACK_GOOD = "👍"
FEEDBACK_BAD = "👎"
MAX_TRACKED_REPLIES = 500  # cap the feedback-tracking set so it can't grow forever


class ClankyCat(discord.Client):
    def __init__(
        self,
        channel_id: int,
        spotify_id: str | None,
        spotify_secret: str | None,
        video_cookies_file: str | None = None,
        github_token: str | None = None,
        github_repo: str | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # required to read message text
        super().__init__(intents=intents)
        self.channel_id = channel_id
        self._spotify_id = spotify_id
        self._spotify_secret = spotify_secret
        self._video_cookies_file = video_cookies_file
        self._github_token = github_token
        self._github_repo = github_repo
        self.http_session: aiohttp.ClientSession | None = None
        self.platforms: list[MusicPlatform] = []
        self.videos: VideoDownloader | None = None
        self.issues: IssueReporter | None = None
        self._runs: dict[int, Run] = {}  # reply id -> run awaiting feedback, insertion-ordered

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        self.platforms = [
            SpotifyPlatform(self.http_session, self._spotify_id, self._spotify_secret),
            AppleMusicPlatform(self.http_session),
            YouTubePlatform(),
        ]
        self.videos = VideoDownloader(cookies_file=self._video_cookies_file)
        self.issues = IssueReporter(self.http_session, self._github_token, self._github_repo)
        if not self.issues.enabled:
            log.warning("GitHub issue reporting is disabled (set GITHUB_TOKEN and GITHUB_REPO)")
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
        await self._handle_music(message)
        await self._handle_video(message)

    async def _handle_music(self, message: discord.Message) -> None:
        matched = self._match_platform(message.content)
        if matched is None:
            return
        source, link = matched
        if not source.enabled:
            return  # can't resolve the source song

        run_id = make_run_id(message.id, source.key, link.track_id)
        log.info("Run %s: %s link from %s: %s", run_id, source.key, message.author, link.url)

        try:
            song = await source.get_song(link.track_id)
        except Exception:
            log.exception("Could not resolve %s, leaving message untouched", link.url)
            return

        others = [p for p in self.platforms if p is not source]
        results = await asyncio.gather(*(p.search(song) for p in others))
        links = {p.key: url for p, url in zip(others, results)}
        links[source.key] = link.url

        reply = await message.reply(
            embed=self._build_embed(song, links, run_id), mention_author=False
        )

        self._runs[reply.id] = Run(
            run_id=run_id,
            song=song,
            source_label=source.label,
            source_url=link.url,
            results={p.label: links.get(p.key) for p in self.platforms},
            message_url=message.jump_url,
        )
        while len(self._runs) > MAX_TRACKED_REPLIES:
            self._runs.pop(next(iter(self._runs)))

        for emoji in (FEEDBACK_GOOD, FEEDBACK_BAD):
            try:
                await reply.add_reaction(emoji)
            except discord.HTTPException:
                log.warning("Could not seed %s reaction on reply %s", emoji, reply.id)

    async def _handle_video(self, message: discord.Message) -> None:
        link = self.videos.match(message.content) if self.videos else None
        if link is None:
            return

        log.info("%s video from %s: %s", link.site, message.author, link.url)
        max_bytes = message.guild.filesize_limit if message.guild else discord.utils.DEFAULT_FILE_SIZE_LIMIT_BYTES

        try:
            async with message.channel.typing():
                async with self.videos.download(link, max_bytes) as path:
                    # Open explicitly so the handle is closed before the temp dir is removed.
                    with path.open("rb") as fp:
                        await message.reply(
                            file=discord.File(fp, filename=f"{link.site}.mp4"), mention_author=False
                        )
        except VideoError as exc:
            log.warning("Could not repost %s, leaving message untouched: %s", link.url, exc)
        except Exception:
            log.exception("Could not repost %s, leaving message untouched", link.url)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        run = self._runs.get(payload.message_id)
        if (
            run is None
            or payload.user_id == (self.user.id if self.user else None)
            or str(payload.emoji) not in (FEEDBACK_GOOD, FEEDBACK_BAD)
        ):
            return
        verdict = "good" if str(payload.emoji) == FEEDBACK_GOOD else "bad"
        log.info(
            "Feedback on run %s (reply %s) from user %s: %s match",
            run.run_id,
            payload.message_id,
            payload.user_id,
            verdict,
        )
        if verdict == "bad" and self.issues is not None:
            reporter = payload.member.display_name if payload.member else f"user {payload.user_id}"
            try:
                await self.issues.report_bad_match(run, reporter)
            except (GitHubError, aiohttp.ClientError) as exc:
                log.warning("Could not file GitHub issue for run %s: %s", run.run_id, exc)

    def _build_embed(self, song: Song, links: dict[str, str | None], run_id: str) -> discord.Embed:
        title = f"{song.title} — {song.artist}" if song.artist else song.title
        embed = discord.Embed(title=title, color=EMBED_COLOR)
        if song.thumbnail_url:
            embed.set_thumbnail(url=song.thumbnail_url)

        for platform in self.platforms:
            url = links.get(platform.key)
            value = f"[Listen]({url})" if url else NOT_FOUND
            embed.add_field(name=platform.label, value=value, inline=True)
        embed.set_footer(text=f"run {run_id}")
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
        video_cookies_file=os.getenv("VIDEO_COOKIES_FILE") or None,
        github_token=os.getenv("GITHUB_TOKEN") or None,
        github_repo=os.getenv("GITHUB_REPO") or None,
    )
    bot.run(token)


if __name__ == "__main__":
    main()
