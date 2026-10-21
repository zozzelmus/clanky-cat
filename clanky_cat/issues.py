"""Files a GitHub issue when someone 👎s a match.

Every resolution ("run") gets a deterministic run id, derived from the
Discord message that triggered it and the source track. The id is shown in
the reply's embed footer and written into the issue body, so each issue
traces back to exactly one run and each run produces at most one issue no
matter how many people downvote it.

Dedupe has two layers:
- in memory: run id -> issue number, behind a lock so simultaneous 👎s on
  the same reply can't race each other into two issues;
- on GitHub: before creating, the issue search API is queried for the run
  id, so a restart (which empties the memory cache) doesn't duplicate an
  existing issue.

Needs GITHUB_TOKEN (fine-grained token with Issues: read & write on the
repo) and GITHUB_REPO (owner/name). Without them the reporter is disabled.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass

import aiohttp

from .song import Song

log = logging.getLogger(__name__)

_API = "https://api.github.com"
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_MAX_CACHED_RUNS = 1000  # cap the run -> issue cache so it can't grow forever


def make_run_id(message_id: int, source_key: str, track_id: str) -> str:
    """Deterministic id for one resolution: same message + source track, same id."""
    digest = hashlib.sha256(f"{message_id}:{source_key}:{track_id}".encode()).hexdigest()
    return digest[:16]


@dataclass(frozen=True)
class Run:
    """Everything needed to describe one resolution in an issue."""

    run_id: str
    song: Song
    source_label: str
    source_url: str
    results: dict[str, str | None]  # platform label -> url (None = NOT FOUND)
    message_url: str


class GitHubError(Exception):
    pass


class IssueReporter:
    def __init__(self, session: aiohttp.ClientSession, token: str | None, repo: str | None) -> None:
        self._session = session
        self._repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._issues: dict[str, int] = {}  # run id -> issue number, insertion-ordered
        self._lock = asyncio.Lock()
        self.enabled = bool(token and repo)

    async def report_bad_match(self, run: Run, reporter: str) -> int | None:
        """Return the issue number for *run*, creating it on first report."""
        if not self.enabled:
            return None
        async with self._lock:
            number = self._issues.get(run.run_id)
            if number is None:
                number = await self._find_issue(run.run_id)
            if number is not None:
                log.info("Run %s already reported as issue #%s", run.run_id, number)
            else:
                number = await self._create_issue(run, reporter)
                log.info("Run %s reported as issue #%s", run.run_id, number)
            self._remember(run.run_id, number)
            return number

    def _remember(self, run_id: str, number: int) -> None:
        self._issues[run_id] = number
        while len(self._issues) > _MAX_CACHED_RUNS:
            self._issues.pop(next(iter(self._issues)))

    async def _find_issue(self, run_id: str) -> int | None:
        query = f'repo:{self._repo} is:issue in:body "{run_id}"'
        async with self._session.get(
            f"{_API}/search/issues", params={"q": query, "per_page": 1}, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise GitHubError(f"issue search failed: HTTP {resp.status}")
            data = await resp.json()
        items = data.get("items", [])
        return items[0]["number"] if items else None

    async def _create_issue(self, run: Run, reporter: str) -> int:
        song = run.song
        name = f"{song.title} — {song.artist}" if song.artist else song.title
        rows = "\n".join(
            f"| {label} | {url or 'NOT FOUND'} |" for label, url in run.results.items()
        )
        body = (
            f"Reported via 👎 on the bot's reply by {reporter}.\n\n"
            f"**Song:** {name}\n"
            f"**Source ({run.source_label}):** {run.source_url}\n"
            f"**Discord message:** {run.message_url}\n\n"
            f"| Platform | Result |\n|---|---|\n{rows}\n\n"
            f"Run ID: `{run.run_id}`\n"
        )
        async with self._session.post(
            f"{_API}/repos/{self._repo}/issues",
            json={"title": f"Bad match: {name}", "body": body},
            headers=self._headers,
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status != 201:
                raise GitHubError(f"issue create failed: HTTP {resp.status}")
            data = await resp.json()
        return data["number"]
