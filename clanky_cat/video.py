"""Short-form video reposting — TikTok videos and Instagram Reels via yt-dlp.

The video is downloaded as an mp4 (H.264 preferred, so Discord plays it
inline) into a throwaway temp directory. If it's bigger than the upload
limit it's re-encoded with ffmpeg to fit; if that can't produce a watchable
file the download is rejected with VideoTooLargeError.

ffmpeg is optional but strongly recommended: without it yt-dlp can't merge
separate video/audio streams (common on Instagram) and oversized videos
can't be shrunk.

yt-dlp is synchronous, so all work is pushed to a worker thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

log = logging.getLogger(__name__)

_SITES: dict[str, re.Pattern[str]] = {
    # tiktok.com/@user/video/<id>, tiktok.com/t/<code>, vm./vt.tiktok.com/<code>
    "tiktok": re.compile(
        r"https?://(?:(?:www|m)\.)?tiktok\.com/(?:@[\w.-]+/video/\d+|t/[A-Za-z0-9]+|v/\d+)[^\s<>]*"
        r"|https?://(?:vm|vt)\.tiktok\.com/[A-Za-z0-9]+[^\s<>]*",
        re.IGNORECASE,
    ),
    # instagram.com/reel/<code>, /reels/<code>, /<user>/reel/<code>
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?reels?/[A-Za-z0-9_-]+[^\s<>]*",
        re.IGNORECASE,
    ),
}

DOWNLOAD_CAP_BYTES = 200 * 1024 * 1024  # never pull more than this from the source
_SIZE_HEADROOM = 0.92  # target this fraction of the limit when re-encoding (bitrate overshoot)
_AUDIO_BPS = 96_000
_MIN_VIDEO_BPS = 150_000  # below this the re-encode is unwatchable, so give up
_MAX_DIMENSION = 1280  # longest side when re-encoding to fit
_FFMPEG_TIMEOUT = 300


class VideoError(Exception):
    pass


class VideoDownloadError(VideoError):
    pass


class VideoTooLargeError(VideoError):
    pass


@dataclass(frozen=True)
class VideoLink:
    url: str
    site: str  # key of _SITES, e.g. "tiktok"


class VideoDownloader:
    def __init__(self, cookies_file: str | None = None, max_concurrent: int = 2) -> None:
        self._cookies_file = cookies_file
        self._ffmpeg = shutil.which("ffmpeg")
        self._ffprobe = shutil.which("ffprobe")
        self._slots = asyncio.Semaphore(max_concurrent)
        if not self._ffmpeg:
            log.warning(
                "ffmpeg not found on PATH: videos with separate audio/video streams "
                "may fail and oversized videos can't be shrunk to fit Discord"
            )

    def match(self, text: str) -> VideoLink | None:
        for site, pattern in _SITES.items():
            m = pattern.search(text)
            if m:
                return VideoLink(url=m.group(0), site=site)
        return None

    @contextlib.asynccontextmanager
    async def download(self, link: VideoLink, max_bytes: int) -> AsyncIterator[Path]:
        """Yield the path of an mp4 no larger than *max_bytes*.

        The file lives in a temp directory that is deleted when the context
        exits, so upload it inside the ``async with`` block.
        """
        with tempfile.TemporaryDirectory(prefix="clanky_cat_") as tmp:
            async with self._slots:
                path = await asyncio.to_thread(self._download_sync, link.url, Path(tmp), max_bytes)
            yield path

    def _ydl_options(self, workdir: Path) -> dict:
        options = {
            "outtmpl": str(workdir / "source.%(ext)s"),
            # Without ffmpeg only single-file formats work (no merging).
            "format": "bv*+ba/b" if self._ffmpeg else "b[ext=mp4]/b",
            # Prefer H.264 in mp4 — the combination every Discord client plays inline.
            "format_sort": ["vcodec:h264", "res:1080", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
            "max_filesize": DOWNLOAD_CAP_BYTES,
            "noplaylist": True,
            "playlist_items": "1",  # carousels: first video only
            "socket_timeout": 30,
            "logger": logging.getLogger(f"{__name__}.yt_dlp"),  # instead of raw stderr
            "noprogress": True,
        }
        if self._ffmpeg:
            options["ffmpeg_location"] = self._ffmpeg
        if self._cookies_file:
            options["cookiefile"] = self._cookies_file
        return options

    def _download_sync(self, url: str, workdir: Path, max_bytes: int) -> Path:
        try:
            with YoutubeDL(self._ydl_options(workdir)) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadError as exc:
            raise VideoDownloadError(str(exc)) from exc
        if info and info.get("entries"):
            info = next(iter(info["entries"]), None)

        requested = (info or {}).get("requested_downloads") or [{}]
        filepath = requested[0].get("filepath")
        if not filepath or not Path(filepath).is_file():
            # yt-dlp skips (without raising) files over max_filesize.
            raise VideoDownloadError(f"nothing downloaded for {url} (larger than the download cap?)")
        source = Path(filepath)

        if source.suffix.lower() == ".mp4" and source.stat().st_size <= max_bytes:
            return source
        if not self._ffmpeg:
            if source.stat().st_size > max_bytes:
                raise VideoTooLargeError(
                    f"{source.stat().st_size} bytes exceeds the {max_bytes} byte limit (no ffmpeg to shrink it)"
                )
            return source  # not mp4, but nothing we can do without ffmpeg

        output = workdir / "video.mp4"
        self._transcode_sync(source, output, max_bytes, info.get("duration"))
        if output.stat().st_size > max_bytes:
            raise VideoTooLargeError(f"re-encoded to {output.stat().st_size} bytes, still over {max_bytes}")
        return output

    def _transcode_sync(self, source: Path, output: Path, max_bytes: int, duration: float | None) -> None:
        """Re-encode *source* to H.264/AAC mp4, at a bitrate that fits *max_bytes* if needed."""
        command = [self._ffmpeg, "-y", "-loglevel", "error", "-i", str(source)]
        if source.stat().st_size <= max_bytes:
            # Already small enough, just not mp4: convert at normal quality.
            command += ["-c:v", "libx264", "-crf", "23"]
        else:
            duration = duration or self._probe_duration(source)
            if not duration:
                raise VideoTooLargeError("too large and the duration is unknown, can't pick a bitrate")
            video_bps = int(max_bytes * 8 * _SIZE_HEADROOM / duration) - _AUDIO_BPS
            if video_bps < _MIN_VIDEO_BPS:
                raise VideoTooLargeError(f"{duration:.0f}s is too long to fit in {max_bytes} bytes")
            command += [
                "-c:v", "libx264",
                "-b:v", str(video_bps),
                "-maxrate", str(video_bps),
                "-bufsize", str(video_bps * 2),
                "-vf",
                f"scale='min({_MAX_DIMENSION},iw)':'min({_MAX_DIMENSION},ih)'"
                ":force_original_aspect_ratio=decrease:force_divisible_by=2",
            ]  # fmt: skip
        command += [
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", str(_AUDIO_BPS),
            "-movflags", "+faststart",
            str(output),
        ]  # fmt: skip
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
        except subprocess.CalledProcessError as exc:
            raise VideoDownloadError(f"ffmpeg failed: {exc.stderr.decode(errors='replace').strip()}") from exc
        except subprocess.TimeoutExpired as exc:
            raise VideoDownloadError("ffmpeg timed out") from exc

    def _probe_duration(self, path: Path) -> float | None:
        if not self._ffprobe:
            return None
        result = subprocess.run(
            [self._ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
        )
        try:
            return float(result.stdout.strip())
        except ValueError:
            return None
