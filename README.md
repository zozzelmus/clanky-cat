# Clanky Cat

Discord bot that watches one channel for music links. When someone posts a
Spotify, Apple Music, or YouTube song link, the bot deletes their message and
replaces it with an embed linking the song on **all three platforms**
(crediting the original poster). Platforms where the song can't be matched
show `NOT FOUND`.

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
4. The embed is posted **before** the original message is deleted, so a
   missing permission never loses the song.

Links that don't identify a single track (playlists, album pages without a
track, channels) are left untouched, as are messages the bot can't resolve
(network errors etc.).

## Setup

1. **Create the Discord bot** at https://discord.com/developers/applications
   - Bot tab: enable the **Message Content Intent**, copy the token.
   - Invite it with the `bot` scope and these permissions:
     **View Channel, Send Messages, Manage Messages, Embed Links**.

2. **Create a Spotify app** (free) at https://developer.spotify.com/dashboard
   and copy its Client ID and Client Secret. (Optional — without it,
   Spotify links are ignored and Spotify always shows `NOT FOUND`.)

3. **Configure**

   ```
   copy .env.example .env
   ```

   Fill in `DISCORD_TOKEN`, `CHANNEL_ID` (enable Developer Mode in Discord,
   right-click the channel → Copy Channel ID), and the Spotify credentials.

4. **Install & run**

   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python -m clanky_cat
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

## Roadmap

- AI-backed search fallback (local Ollama instance) for songs the
  deterministic search can't match — out of scope for now. Natural shape:
  a wrapper strategy that delegates to a platform's `search()` and only
  invokes the AI when it returns `None`, so `bot.py` needs no changes.
