"""Quick sorts: reorder a playlist by one property, without the journey machinery."""

from __future__ import annotations

from typing import Any

from .genres import primary_group, song_genres
from .journey import camelot, energy, tempo

MODES = {
    "original": "Original order",
    "genre_bpm": "Genre, then BPM",
    "bpm_up": "BPM: slow → fast",
    "bpm_down": "BPM: fast → slow",
    "energy_up": "Energy: calm → intense",
    "energy_down": "Energy: intense → calm",
    "key": "Key: harmonic (Camelot wheel)",
    "year_up": "Year: old → new",
    "year_down": "Year: new → old",
    "artist": "Artist: A → Z",
}

SHORT = {
    "genre_bpm": "by genre", "bpm_up": "by BPM", "bpm_down": "by BPM (fast first)",
    "energy_up": "by energy", "energy_down": "by energy (intense first)", "key": "by key",
    "year_up": "by year", "year_down": "by year (newest first)", "artist": "by artist",
}

LAST = float("inf")


def sort_entries(library: dict[str, Any], entries: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Return the entries in the new order. Songs missing the value being sorted on go last,
    in their original order."""
    tracks = library["tracks"]
    if mode == "original":
        return list(entries)

    def year(t):
        y = (t.get("album") or {}).get("release_date", "")[:4]
        return int(y) if y.isdigit() else None

    def key_of(item):
        i, e = item
        t = tracks.get(e["id"])
        if t is None:
            return (1, LAST, i)
        if mode in ("bpm_up", "bpm_down"):
            v = tempo(t)
            return (v is None, (v if mode == "bpm_up" else -v) if v else LAST, i)
        if mode in ("energy_up", "energy_down"):
            v = energy(t)
            return (v is None, (v if mode == "energy_up" else -v) if v is not None else LAST, i)
        if mode == "key":
            c = camelot(t)
            return (c is None, (c[0], c[1], tempo(t) or 0) if c else (99, "", 0), i)
        if mode in ("year_up", "year_down"):
            v = year(t)
            return (v is None, (v if mode == "year_up" else -v) if v else LAST, i)
        if mode == "artist":
            name = (t["artists"][0]["name"] if t["artists"] else "").lower()
            return (not name, (name, t["album"].get("release_date", ""), t["name"].lower()), i)
        if mode == "genre_bpm":
            family = primary_group(library, t, "broad")
            exact = (song_genres(library, t) or [""])[0]
            return (family is None, (family or "", exact, tempo(t) or LAST), i)
        return (0, 0, i)

    return [e for _, e in sorted(enumerate(entries), key=key_of)]
