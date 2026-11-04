"""Add genres and audio features (BPM, energy, mood, key) to a library snapshot.

Spotify stopped giving new apps per-song audio features in late 2024 and has been thinning
artist genres since 2025, so:
  * genres come from Spotify's artist data, filled in from Last.fm tags when a key is set
  * BPM, energy, mood etc. come from ReccoBeats, a free API that looks songs up by Spotify ID

Everything fetched is cached on disk, so a re-sync only asks about new songs and artists.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests
from spotipy.exceptions import SpotifyException

RECCOBEATS_URL = "https://api.reccobeats.com/v1/audio-features"
LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
FEATURE_KEYS = (
    "tempo", "energy", "danceability", "valence", "acousticness",
    "instrumentalness", "speechiness", "liveness", "loudness", "key", "mode",
)
RECCO_BATCH = 40
RETRY_EMPTY_AFTER = 3 * 24 * 3600  # look again for songs/artists that had no data after 3 days
KEY_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]

# Last.fm tags that describe the listener rather than the music.
NON_GENRE_TAGS = {
    "seen live", "favorites", "favourite", "favorite", "my favorite", "love", "awesome", "beautiful",
    "albums i own", "under 2000 listeners", "spotify", "male vocalists", "female vocalists",
    "male vocalist", "female vocalist", "singer-songwriter",
}

Progress = Callable[[str, int, int], None]
SPOTIFY_TRACK_RE = re.compile(r"track[/:]([A-Za-z0-9]{22})")


def _noop(message: str, done: int, total: int) -> None:
    pass


def key_label(features: dict[str, Any] | None) -> str:
    if not features or features.get("key") in (None, -1):
        return ""
    name = KEY_NAMES[int(features["key"]) % 12]
    return f"{name} {'major' if features.get('mode') == 1 else 'minor'}"


# ---------------------------------------------------------------- cache


def load_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("features", {})   # track id -> features dict, or None when not found
    data.setdefault("spotify_genres", {})  # artist id -> list
    data.setdefault("lastfm_tags", {})  # artist name (lowercase) -> list
    data.setdefault("empty_since", {})  # "kind:id" -> when a lookup found nothing
    return data


def _needs_lookup(cache: dict[str, Any], kind: str, key: str, now: float) -> bool:
    """True when we have never looked, or last time found nothing and that was a while ago."""
    if key not in cache[kind]:
        return True
    if cache[kind][key]:
        return False
    since = cache["empty_since"].get(f"{kind}:{key}")
    return since is None or now - since > RETRY_EMPTY_AFTER


def _store(cache: dict[str, Any], kind: str, results: dict[str, Any], now: float) -> None:
    for key, value in results.items():
        cache[kind][key] = value
        if value:
            cache["empty_since"].pop(f"{kind}:{key}", None)
        else:
            cache["empty_since"][f"{kind}:{key}"] = now


def save_cache(cache: dict[str, Any], path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- sources


def fetch_spotify_genres(sp, artist_ids: list[str], progress: Progress = _noop) -> dict[str, list[str]]:
    """Artist genres from Spotify. Uses the 50-at-a-time endpoint, and falls back to one artist
    per request if Spotify refuses the bulk one (it does for some apps). Raises if both fail."""
    out: dict[str, list[str]] = {}
    bulk_ok = True
    for start in range(0, len(artist_ids), 50):
        batch = artist_ids[start:start + 50]
        artists = None
        if bulk_ok:
            try:
                artists = (sp.artists(batch) or {}).get("artists", [])
            except SpotifyException:
                bulk_ok = False
        if artists is None:
            artists = []
            for aid in batch:
                try:
                    artists.append(sp.artist(aid))
                except SpotifyException:
                    if not out and not artists:
                        raise  # the single-artist endpoint is refused too
        for artist in artists:
            if artist and artist.get("id"):
                out[artist["id"]] = list(artist.get("genres") or [])
        progress("Reading artist genres", min(start + 50, len(artist_ids)), len(artist_ids))
    return out


def _get(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    for attempt in range(4):
        try:
            resp = session.get(url, params=params, timeout=20)
        except requests.RequestException:
            time.sleep(1 + attempt)
            continue
        if resp.status_code == 429:
            time.sleep(float(resp.headers.get("Retry-After", 2 + attempt)))
            continue
        if resp.status_code >= 500:
            time.sleep(1 + attempt)
            continue
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None
    return None


def _spotify_id(item: dict[str, Any]) -> str | None:
    for field in ("href", "uri", "spotifyId", "spotify_id"):
        value = item.get(field)
        if isinstance(value, str):
            if len(value) == 22 and field in ("spotifyId", "spotify_id"):
                return value
            match = SPOTIFY_TRACK_RE.search(value)
            if match:
                return match.group(1)
    return None


def fetch_audio_features(
    session: requests.Session, track_ids: list[str], progress: Progress = _noop
) -> dict[str, dict[str, Any] | None]:
    """Look up audio features on ReccoBeats. Songs it doesn't know map to None."""
    out: dict[str, dict[str, Any] | None] = {}
    for start in range(0, len(track_ids), RECCO_BATCH):
        batch = track_ids[start:start + RECCO_BATCH]
        data = _get(session, RECCOBEATS_URL, {"ids": ",".join(batch)})
        if data is None:
            progress("Reading BPM and energy", min(start + RECCO_BATCH, len(track_ids)), len(track_ids))
            continue  # leave them uncached so the next sync retries
        items = data.get("content") if isinstance(data, dict) else data
        for item in items or []:
            sid = _spotify_id(item)
            if sid is None and len(batch) == 1:
                sid = batch[0]
            if sid in batch:
                out[sid] = {k: item.get(k) for k in FEATURE_KEYS if item.get(k) is not None}
        for tid in batch:
            out.setdefault(tid, None)
        progress("Reading BPM and energy", min(start + RECCO_BATCH, len(track_ids)), len(track_ids))
    return out


def fetch_lastfm_tags(
    session: requests.Session, api_key: str, artist_names: list[str], progress: Progress = _noop
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for i, name in enumerate(artist_names, start=1):
        data = _get(session, LASTFM_URL, {
            "method": "artist.gettoptags", "artist": name, "api_key": api_key,
            "format": "json", "autocorrect": 1,
        })
        if data is not None and "error" not in data:
            tags = (data.get("toptags") or {}).get("tag") or []
            if isinstance(tags, dict):
                tags = [tags]
            picked = []
            for tag in tags:
                label = (tag.get("name") or "").strip().lower()
                if not label or label in NON_GENRE_TAGS or int(tag.get("count") or 0) < 10:
                    continue
                if label.isdigit() or label.endswith("s") and label[:-1].isdigit():  # "2010s", "1999"
                    continue
                picked.append(label)
                if len(picked) == 5:
                    break
            out[name.lower()] = picked
        progress("Filling in genres from Last.fm", i, len(artist_names))
        time.sleep(0.2)  # Last.fm asks for no more than ~5 requests per second
    return out


# ---------------------------------------------------------------- main entry


def enrich(
    library: dict[str, Any],
    sp,
    cache_path: Path,
    lastfm_key: str | None = None,
    progress: Progress = _noop,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fill in library["artists"] and each track's "features". Returns the same dict."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "Loom/0.2 (personal playlist tool)")
    cache = load_cache(cache_path)
    tracks = library["tracks"]
    now = time.time()
    problems: list[str] = []

    artists: dict[str, str] = {}
    for t in tracks.values():
        for a in t["artists"]:
            if a.get("id"):
                artists[a["id"]] = a["name"]

    missing_artists = [a for a in artists if _needs_lookup(cache, "spotify_genres", a, now)]
    if missing_artists:
        try:
            _store(cache, "spotify_genres", fetch_spotify_genres(sp, missing_artists, progress), now)
        except Exception as exc:  # genres are best effort; keep going with BPM
            problems.append(f"Spotify refused the artist genre lookup ({_describe(exc)}).")
        save_cache(cache, cache_path)

    missing_tracks = [tid for tid in tracks if _needs_lookup(cache, "features", tid, now)]
    if missing_tracks:
        try:
            _store(cache, "features", fetch_audio_features(session, missing_tracks, progress), now)
        except Exception as exc:
            problems.append(f"Couldn't reach ReccoBeats for BPM data ({_describe(exc)}).")
        save_cache(cache, cache_path)

    if lastfm_key:
        need = sorted({
            name for aid, name in artists.items()
            if not cache["spotify_genres"].get(aid)
            and _needs_lookup(cache, "lastfm_tags", name.lower(), now)
        })
        if need:
            try:
                _store(cache, "lastfm_tags", fetch_lastfm_tags(session, lastfm_key, need, progress), now)
            except Exception as exc:
                problems.append(f"Couldn't reach Last.fm for genres ({_describe(exc)}).")
            save_cache(cache, cache_path)

    library["artists"] = {}
    for aid, name in artists.items():
        spotify = cache["spotify_genres"].get(aid) or []
        lastfm = cache["lastfm_tags"].get(name.lower()) or []
        library["artists"][aid] = {
            "name": name,
            "genres": spotify or lastfm,
            "genre_source": "spotify" if spotify else ("lastfm" if lastfm else None),
        }
    for tid, t in tracks.items():
        t["features"] = cache["features"].get(tid)
    library["enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    library["enrich_problems"] = problems
    return library


def _describe(exc: Exception) -> str:
    if isinstance(exc, SpotifyException):
        return f"HTTP {exc.http_status}: {exc.msg}".strip()
    return f"{type(exc).__name__}: {exc}"


def track_genres(library: dict[str, Any], track: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for a in track["artists"]:
        for g in (library.get("artists", {}).get(a.get("id")) or {}).get("genres", []):
            if g not in seen:
                seen.append(g)
    return seen
