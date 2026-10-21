# Clanky Cat

Discord bot that watches one channel for music links. When someone posts a
Spotify, Apple Music, or YouTube song link, the bot replies to their message
with an embed linking the song on **all three platforms**. The original
message is left in place. Platforms where the song can't be matched show
`NOT FOUND`, and the reply carries 👍/👎 reactions so people can rate the
match.

It also reposts **TikTok videos and Instagram Reels**: the video is
downloaded as an mp4 and replied to the message as an attachment, so it
plays directly in Discord.

Resolution is fully deterministic — no AI involved:

| Platform | Metadata (source link) | Search (target link) | Credentials |
|---|---|---|---|
| Spotify | Spotify Web API | Spotify Web API | free Spotify app (client id/secret) |
| Apple Music | iTunes Lookup API | iTunes Search API | none |
| YouTube | ytmusicapi | ytmusicapi | none |

> Why not Odesli/song.link? As of mid-2026 its API no longer returns Apple
> Music or YouTube matches — exactly the platforms we need — so the bot
> queries each platform directly.

## How a message is handled

1. A non-bot message in the watched channel is scanned for the first
   Spotify track / Apple Music song / YouTube video link.
2. Title + artist are fetched from the source platform.
3. The other two platforms are searched concurrently. A result only counts
   if its title matches the source title (normalized containment or
   difflib similarity ≥ 0.6) — otherwise `NOT FOUND`.
4. The bot replies to the original message with the embed (without pinging
   the poster) and seeds it with 👍 and 👎 reactions. If a reaction can't be
   added, a warning is logged and the reply stays.
5. When someone clicks 👍 or 👎 on one of those replies, the vote is logged
   at INFO level, e.g. `Feedback on reply <id> from user <id>: good match`.
   Only the 500 most recent replies are tracked, in memory, so votes on
   older replies or on replies from before a restart aren't logged. Removing
   a vote isn't logged either.

Links that don't identify a single track (playlists, album pages without a
track, channels) are left untouched, as are messages the bot can't resolve
(network errors etc.).

## How a video link is handled

1. A message in the watched channel is scanned for the first TikTok video
   (`tiktok.com/@user/video/…`, `vm.tiktok.com/…`, `tiktok.com/t/…`) or
   Instagram Reel (`instagram.com/reel/…`) link.
2. [yt-dlp](https://github.com/yt-dlp/yt-dlp) downloads it as an mp4,
   preferring H.264 so every Discord client plays it inline.
3. If it's over the server's upload limit (10 MB unless boosted), ffmpeg
   re-encodes it to fit. Videos too long to fit at a watchable bitrate
   are skipped.
4. The bot replies to the message with the mp4 attached.

Failures (private/removed videos, Instagram asking for a login, etc.) are
logged and the message is left untouched.

TikTok and Instagram change their sites often and yt-dlp keeps up with
frequent releases — if downloads start failing, update it first:
`pip install -U "yt-dlp[default,curl-cffi]"` (or rebuild the Docker image).

## Setup

1. **Create the Discord bot** at https://discord.com/developers/applications
   - Bot tab: enable the **Message Content Intent**, copy the token.
   - Invite it with the `bot` scope and these permissions:
     **View Channel, Send Messages, Read Message History, Embed Links,
     Attach Files, Add Reactions**. (Discord requires Read Message History
     to reply to a message or add a reaction to it. Manage Messages is not
     needed, since the bot never deletes anything.)

2. **Create a Spotify app** (free) at https://developer.spotify.com/dashboard
   and copy its Client ID and Client Secret. (Optional — without it,
   Spotify links are ignored and Spotify always shows `NOT FOUND`.)

3. **Configure**

   ```
   copy .env.example .env
   ```

   Fill in `DISCORD_TOKEN`, `CHANNEL_ID` (enable Developer Mode in Discord,
   right-click the channel → Copy Channel ID), and the Spotify credentials.

   Instagram frequently blocks anonymous Reel downloads. If they fail, export
   cookies from a browser logged in to Instagram (Netscape `cookies.txt`
   format, e.g. with a "Get cookies.txt" extension), save the file next to
   `.env`, and set `VIDEO_COOKIES_FILE=cookies.txt`. With Docker, uncomment
   the volume in `docker-compose.yml` and use `/app/cookies.txt` instead.
   Use a throwaway account — the cookies are a login session.

4. **Install ffmpeg** (for the video feature; the Docker image already
   has it). Windows: `winget install ffmpeg`, then open a new terminal so
   it's on `PATH`. Without it, many Instagram Reels fail (separate audio and
   video streams can't be merged) and oversized videos can't be shrunk.

5. **Install & run**

   Windows, no Docker — `start.bat` (or `start.ps1`) creates the venv,
   installs dependencies, and starts the bot. Or manually:

   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python -m clanky_cat
   ```

   Docker — the container reads its config from `.env` at runtime (the
   file is never baked into the image) and restarts automatically unless
   stopped:

   ```
   docker compose up -d --build
   docker compose logs -f   # watch it log in
   docker compose down      # stop
   ```

## Project layout

Platforms are strategies: each implements the `MusicPlatform` interface
(`match` / `get_song` / `search`) and owns its own link regexes and API
client. The bot only talks to the interface — adding a platform is one new
file plus one entry in the registry in `bot.py`'s `setup_hook`.

- `clanky_cat/platform_base.py` — `MusicPlatform` interface + `LinkMatch`
- `clanky_cat/bot.py` — Discord client, registry, orchestration, embeds
- `clanky_cat/song.py` — shared `Song` model + deterministic title matching
- `clanky_cat/spotify.py` — Spotify Web API strategy (client-credentials)
- `clanky_cat/apple.py` — iTunes Lookup/Search API strategy
- `clanky_cat/youtube.py` — ytmusicapi strategy (runs in a worker thread)
- `clanky_cat/video.py` — TikTok / Instagram Reel downloader (yt-dlp + ffmpeg)

## Roadmap

- AI-backed search fallback (local Ollama instance) for songs the
  deterministic search can't match — out of scope for now. Natural shape:
  a wrapper strategy that delegates to a platform's `search()` and only
  invokes the AI when it returns `None`, so `bot.py` needs no changes.
