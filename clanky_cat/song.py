"""Shared song metadata model and deterministic match checking."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class Song:
    title: str
    artist: str | None = None
    thumbnail_url: str | None = None

    @property
    def query(self) -> str:
        return f"{self.artist} {self.title}" if self.artist else self.title


_PARENTHETICAL = re.compile(r"[\(\[【][^\)\]】]*[\)\]】]")  # (Official Video), [Remaster], 【MV】, ...
_NON_WORD = re.compile(r"[\W_]+")  # Unicode-aware: keeps letters/digits of any script
_LATIN_DIACRITICS = re.compile(r"[\u0300-\u036f]")


def _normalize(text: str) -> str:
    # NFKD folds full-width forms (Ｂｅａｔ, （）) to ASCII and splits accents off
    # Latin letters so "Beyoncé" == "Beyonce". Only the Latin combining block is
    # dropped: Japanese dakuten (が = か + U+3099) must survive, so NFC re-joins them.
    text = unicodedata.normalize("NFKD", text)
    text = unicodedata.normalize("NFC", _LATIN_DIACRITICS.sub("", text))
    text = _PARENTHETICAL.sub(" ", text.lower())
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def titles_match(expected: str, candidate: str, threshold: float = 0.6) -> bool:
    """Deterministic sanity check that a search result is the same song.

    Accepts containment either way (handles "Song" vs "Song - 2011 Remaster")
    or a difflib similarity above *threshold*.
    """
    a, b = _normalize(expected), _normalize(candidate)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold
