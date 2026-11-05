"""Discover: find popular songs that aren't in your library yet, like "80s rock".

Two sources, merged and ranked:
  * Last.fm tag charts (tag.gettoptracks, e.g. the "80s rock" tag). This is the popularity signal,
    since Spotify stopped giving new apps track popularity in Feb 2026. Each Last.fm hit is then
    looked up on Spotify to get a playable song.
  * Spotify search with filters (genre:"rock" year:1980-1989). New apps get at most 10 results
    per request, so it pages.

Results are kept apart from your library (in discover.json) so they never mix into your own
songs, stats or genre playlists unless you choose to use them.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from . import enrich
from .library import _track_record

Progress = Callable[[str, int, int], None]

DECADES = {f"{y}s": (y, y + 9) for y in range(1950, 2030, 10)}
GENRE_SUGGESTIONS = ["rock", "classic rock", "country", "hip hop", "pop", "metal", "punk", "alternative",
                     "indie", "r&b", "soul", "blues", "folk", "americana", "red dirt", "southern rock",
                     "grunge", "hard rock", "rap", "trap", "electronic", "house", "jazz", "reggae"]
SEARCH_PAGE = 10        # Spotify's cap per search request for development-mode apps
MAX_SEARCH_PAGES = 12
PAUSE = 0.12            # between Spotify calls, to stay well clear of rate limits


def _noop(message: str, done: int, total: int) -> None:
    pass


# ---------------------------------------------------------------- matching helpers

_VERSION = re.compile(r"\s*[\(\[\-–][^\)\]]*\b(remaster(ed)?|live|mono|stereo|version|edit|mix|deluxe|"
                      r"anniversary|bonus|demo)\b[^\)\]]*[\)\]]?\s*$", re.I)
_FEAT = re.compile(r"\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\b.*$", re.I)


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\s+-\s+.*$", "", text) if " - " in text else text  # "Song - 2011 Remaster"
    text = _VERSION.sub("", text)
    text = _FEAT.sub("", text)
    text = re.sub(r"^the\s+", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def song_key(title: str, artist: str) -> tuple[str, str]:
    return norm(artist), norm(title)


def owned_keys(library: dict[str, Any] | None) -> tuple[set[str], set[tuple[str, str]]]:
    if not library:
        return set(), set()
    ids, keys = set(), set()
    for t in library.get("tracks", {}).values():
        ids.add(t["id"])
        if t.get("artists"):
            keys.add(song_key(t["name"], t["artists"][0]["name"]))
    return ids, keys


# ---------------------------------------------------------------- sources


def lastfm_tags(genre: str, decade: str | None) -> list[str]:
    """Which Last.fm tags to read, best first. Last.fm tags decades as "80s"."""
    genre = (genre or "").strip().lower()
    short = decade[2:] if decade else ""  # "1980s" -> "80s"
    if genre and short:
        return [f"{short} {genre}", f"{genre} {short}"]
    if genre:
        return [genre]
    return [short] if short else []


def lastfm_top(session, api_key: str, tags: list[str], want: int) -> list[tuple[str, str]]:
    """(title, artist) pairs from the first tag chart that has enough songs, most popular first."""
    best: list[tuple[str, str]] = []
    for tag in tags:
        data = enrich._lastfm(session, {"method": "tag.gettoptracks", "tag": tag,
                                        "limit": min(max(want * 2, 50), 500), "api_key": api_key})
        items = ((data or {}).get("tracks") or {}).get("track") or []
        pairs = [(it.get("name") or "", (it.get("artist") or {}).get("name") or "") for it in items]
        pairs = [p for p in pairs if p[0] and p[1]]
        if len(pairs) > len(best):
            best = pairs
        if len(best) >= want:
            break
    return best


def spotify_query(genre: str, decade: str | None) -> str:
    parts = []
    if genre:
        parts.append(f'genre:"{genre.strip()}"')
    if decade:
        a, b = DECADES[decade]
        parts.append(f"year:{a}-{b}")
    return " ".join(parts)


def spotify_search(sp, query: str, want: int, progress: Progress = _noop) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pages = min(MAX_SEARCH_PAGES, max(2, (want * 2) // SEARCH_PAGE))
    for page in range(pages):
        progress(f"Searching Spotify ({len(out)} songs so far)", page, pages)
        res = sp.search(q=query, type="track", limit=SEARCH_PAGE, offset=page * SEARCH_PAGE)
        items = [t for t in ((res or {}).get("tracks") or {}).get("items") or [] if t and t.get("id")]
        out += items
        if len(items) < SEARCH_PAGE:
            break
        time.sleep(PAUSE)
    return out


def match_on_spotify(sp, title: str, artist: str) -> dict[str, Any] | None:
    """The Spotify track for a Last.fm (title, artist), or None."""
    want_artist, want_title = song_key(title, artist)
    res = sp.search(q=f'track:"{title}" artist:"{artist}"', type="track", limit=5)
    items = [t for t in ((res or {}).get("tracks") or {}).get("items") or [] if t and t.get("id")]
    for t in items:
        names = [norm(a.get("name") or "") for a in t.get("artists") or []]
        if want_artist in names and norm(t.get("name") or "") == want_title:
            return t
    for t in items:  # looser: right artist, title starts the same way ("Song" vs "Song (Live)")
        names = [norm(a.get("name") or "") for a in t.get("artists") or []]
        if want_artist in names and norm(t.get("name") or "").startswith(want_title[:12]):
            return t
    return None


# ---------------------------------------------------------------- the search


def find(sp, genre: str, decade: str | None, want: int = 50, library: dict[str, Any] | None = None,
         skip_owned: bool = True, lastfm_key: str | None = None, session=None,
         progress: Progress = _noop) -> dict[str, Any]:
    """Find `want` popular songs for a genre and/or decade. Returns a discover result:
    {"label", "genre", "decade", "order": [track ids], "tracks": {id: record}, "problems": [...],
     "counts": {"lastfm": n, "spotify": n}}"""
    import requests

    from .spotify import RateLimited

    genre = (genre or "").strip()
    if not genre and not decade:
        raise ValueError("Pick a genre, a decade, or both.")
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "Loom/0.5 (personal playlist tool)")
    owned_ids, owned = owned_keys(library) if skip_owned else (set(), set())
    problems: list[str] = []
    scored: dict[tuple[str, str], dict[str, Any]] = {}  # song key -> {"track", "score", "via"}

    def offer(track: dict[str, Any], score: float, via: str) -> None:
        if not track.get("artists"):
            return
        key = song_key(track["name"], track["artists"][0]["name"])
        if track["id"] in owned_ids or key in owned:
            return
        if key in scored:
            scored[key]["score"] += score * 0.5  # in both sources: a stronger signal
            scored[key]["via"].add(via)
        else:
            scored[key] = {"track": track, "score": score, "via": {via}}

    # 1. Spotify search with filters (relevance order is the only ranking it gives new apps)
    sp_tracks: list[dict[str, Any]] = []
    try:
        sp_tracks = spotify_search(sp, spotify_query(genre, decade), want, progress)
    except RateLimited as exc:
        problems.append(f"{exc}. Showing what was found so far.")
    except Exception as exc:
        problems.append(f"Spotify search failed ({type(exc).__name__}).")
    by_key = {}
    for rank, t in enumerate(sp_tracks):
        offer(t, 0.75 - 0.5 * rank / max(len(sp_tracks), 1), "spotify")
        if t.get("artists"):
            by_key.setdefault(song_key(t["name"], t["artists"][0]["name"]), t)

    # 2. Last.fm tag chart, the popularity signal, each hit matched to a Spotify song
    lf_pairs: list[tuple[str, str]] = []
    if lastfm_key:
        try:
            lf_pairs = lastfm_top(session, lastfm_key, lastfm_tags(genre, decade), want)
        except enrich.LastfmError as exc:
            problems.append(f"Last.fm: {exc}. Check the key with Test key in Settings.")
        except Exception as exc:
            problems.append(f"Couldn't reach Last.fm ({type(exc).__name__}).")
        lookups, budget = 0, int(want * 1.6) + 10
        for rank, (title, artist) in enumerate(lf_pairs):
            if lookups >= budget:
                break
            key = song_key(title, artist)
            if key in owned:
                continue
            score = 1.0 - 0.6 * rank / max(len(lf_pairs), 1)
            track = by_key.get(key)
            if track is None:
                progress(f"Matching Last.fm favourites on Spotify: {title} · {artist}", rank, len(lf_pairs))
                try:
                    track = match_on_spotify(sp, title, artist)
                except RateLimited as exc:
                    problems.append(f"{exc}. Showing what was found so far.")
                    break
                except Exception:
                    track = None
                lookups += 1
                time.sleep(PAUSE)
            if track:
                offer(track, score, "lastfm")
    elif not sp_tracks:
        problems.append("Add a Last.fm key in Settings for better results (it also ranks songs by popularity).")

    ranked = sorted(scored.values(), key=lambda c: -c["score"])[:want]
    records: dict[str, dict[str, Any]] = {}
    for c in ranked:
        rec = _track_record(c["track"])
        rec["found_via"] = sorted(c["via"])
        records[rec["id"]] = rec
    label = " ".join(x for x in ((decade or "")[2:], genre) if x) or "songs"
    return {
        "label": label,
        "genre": genre,
        "decade": decade,
        "order": list(records),
        "tracks": records,
        "artists": {},
        "problems": problems,
        "counts": {"lastfm": len(lf_pairs), "spotify": len(sp_tracks)},
        "found_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def add_song_data(result: dict[str, Any], sp, cache_path: Path, lastfm_key: str | None,
                  progress: Progress = _noop, session=None) -> dict[str, Any]:
    """BPM, energy, key and genre for the found songs, from the same sources as your library."""
    mini = {"tracks": result["tracks"], "liked": [], "playlists": [], "artists": {}}
    enrich.enrich(mini, sp, cache_path, lastfm_key, progress, session)
    result["artists"] = mini["artists"]
    result["problems"] = result.get("problems", []) + (mini.get("enrich_problems") or [])
    if mini.get("lastfm_problem"):
        result["problems"].append(f"Last.fm: {mini['lastfm_problem']}")
    return result


# ---------------------------------------------------------------- storing and mixing in


def save(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def entries(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [{"id": t} for t in (result or {}).get("order", [])]


def merged(library: dict[str, Any] | None, result: dict[str, Any] | None) -> dict[str, Any] | None:
    """A view of the library that also knows the discovered songs (for journeys and tables).
    Liked songs and playlists are unchanged, so "All my songs" still means your own songs."""
    if not library or not result:
        return library
    return {
        **library,
        "tracks": {**result.get("tracks", {}), **library["tracks"]},
        "artists": {**result.get("artists", {}), **library.get("artists", {})},
        "discover": {"label": result.get("label"), "order": result.get("order", [])},
    }
