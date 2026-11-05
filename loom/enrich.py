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
from collections import Counter
import time
from pathlib import Path
from typing import Any, Callable

import requests
from spotipy.exceptions import SpotifyException

from .spotify import Cancelled

RECCOBEATS_URL = "https://api.reccobeats.com/v1/audio-features"
LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
DEEZER_URL = "https://api.deezer.com"
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
    data.setdefault("deezer", {})  # track id -> {"tempo": ...} or None
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


class NoSpotifyGenres(Exception):
    """Spotify answered but gave no genres at all, so asking about more artists is pointless."""


GENRE_PROBE = 30  # if this many artists in a row come back with no genres, stop asking Spotify


ALIASES = {"rnb": "r&b", "r and b": "r&b", "rhythm and blues": "r&b", "hiphop": "hip hop",
           "electronica": "electronic", "alt country": "alt-country", "alternative country": "alt-country",
           "rock n roll": "rock and roll",
           "rock & roll": "rock and roll", "rock'n'roll": "rock and roll"}


def normalize_genre(name: str) -> str:
    """'Hip-Hop' -> 'hip hop', 'rnb' -> 'r&b', so the same genre isn't counted twice."""
    g = " ".join(name.lower().replace("_", " ").split())
    if g not in ("alt-country", "lo-fi", "k-pop", "j-pop", "nu-metal", "post-rock", "post-punk"):
        g = g.replace("-", " ")
    g = " ".join(g.split())
    return ALIASES.get(g, g)


def _clean(genres: list[str]) -> list[str]:
    out: list[str] = []
    for g in genres:
        n = normalize_genre(g)
        if n and n not in out:
            out.append(n)
    return out


def fetch_spotify_genres(
    sp, artist_ids: list[str], progress: Progress = _noop, out: dict[str, list[str]] | None = None,
    allow_single: bool = True,
) -> dict[str, list[str]]:
    """Artist genres from Spotify, written into `out` as they arrive (so a later failure keeps
    what was found). Uses the 50-at-a-time endpoint, falling back to one artist per request if
    Spotify refuses the bulk one. Raises NoSpotifyGenres when Spotify returns none at all."""
    out = {} if out is None else out
    total = len(artist_ids)
    done = 0
    bulk_ok = True
    found_any = False

    def take(artist):
        nonlocal found_any, done
        if artist and artist.get("id"):
            genres = list(artist.get("genres") or [])
            out[artist["id"]] = genres
            found_any = found_any or bool(genres)
        done += 1
        if not found_any and done >= GENRE_PROBE:
            raise NoSpotifyGenres()

    for start in range(0, total, 50):
        batch = artist_ids[start:start + 50]
        if bulk_ok:
            try:
                for artist in (sp.artists(batch) or {}).get("artists", []):
                    take(artist)
                progress("Reading artist genres from Spotify", done, total)
                continue
            except SpotifyException as exc:
                if exc.http_status == 429 or not allow_single:
                    raise
                bulk_ok = False
        for aid in batch:
            try:
                take(sp.artist(aid))
            except SpotifyException as exc:
                if exc.http_status == 429 or not found_any and done == 0:
                    raise  # rate limited, or the single-artist endpoint is refused too
                done += 1
            progress("Reading artist genres from Spotify", done, total)
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
    session: requests.Session, track_ids: list[str], progress: Progress = _noop,
    out: dict[str, dict[str, Any] | None] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Look up audio features on ReccoBeats. Songs it doesn't know map to None."""
    out = {} if out is None else out
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


def fetch_deezer_bpm(
    session: requests.Session, tracks: list[dict[str, Any]], progress: Progress = _noop,
    out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """BPM from Deezer's free API: find the song by ISRC (or by artist + title), then read its bpm.
    Deezer reports 0 when it doesn't know, which we store as a miss."""
    out = {} if out is None else out
    for i, t in enumerate(tracks, start=1):
        deezer_id = None
        if t.get("isrc"):
            data = _get(session, f"{DEEZER_URL}/track/isrc:{t['isrc']}", {})
            if data and not data.get("error"):
                if data.get("bpm"):
                    out[t["id"]] = {"tempo": float(data["bpm"])}
                    progress("Filling in BPM from Deezer", i, len(tracks))
                    time.sleep(0.1)
                    continue
                deezer_id = data.get("id")
        if deezer_id is None and t.get("artists"):
            artist = t["artists"][0]["name"].replace('"', "")
            title = re.sub(r"\s*[(\[-].*$", "", t["name"]).replace('"', "") or t["name"]
            found = _get(session, f"{DEEZER_URL}/search", {"q": f'artist:"{artist}" track:"{title}"', "limit": 1})
            hits = (found or {}).get("data") or []
            deezer_id = hits[0].get("id") if hits else None
        bpm = None
        if deezer_id:
            data = _get(session, f"{DEEZER_URL}/track/{deezer_id}", {})
            bpm = (data or {}).get("bpm") or None
        out[t["id"]] = {"tempo": float(bpm)} if bpm else None
        progress("Filling in BPM from Deezer", i, len(tracks))
        time.sleep(0.1)  # Deezer allows about 50 requests per 5 seconds
    return out


class LastfmError(Exception):
    pass


def _lastfm(session: requests.Session, params: dict[str, Any]) -> dict[str, Any] | None:
    """One Last.fm call. Raises LastfmError for a bad key or a blocked account, returns None for
    "artist not found" and similar per-artist misses."""
    for attempt in range(4):
        try:
            resp = session.get(LASTFM_URL, params={**params, "format": "json"}, timeout=20)
        except requests.RequestException as exc:
            if attempt == 3:
                raise LastfmError(f"couldn't connect ({type(exc).__name__})") from exc
            time.sleep(1 + attempt)
            continue
        try:
            data = resp.json()
        except ValueError:
            data = {}
        code = data.get("error") if isinstance(data, dict) else None
        if code in (10, 26):  # invalid / suspended key
            raise LastfmError(data.get("message") or "invalid API key")
        if code == 29 or resp.status_code == 429:  # rate limited
            time.sleep(2 + attempt)
            continue
        if resp.status_code >= 500 or code in (8, 11, 16):  # temporary trouble on their side
            time.sleep(1 + attempt)
            continue
        return None if code else data
    return None


def check_lastfm_key(api_key: str, session: requests.Session | None = None) -> tuple[bool, str]:
    """Quick test used by the Settings window."""
    session = session or requests.Session()
    try:
        data = _lastfm(session, {"method": "artist.gettoptags", "artist": "Radiohead", "api_key": api_key.strip()})
    except LastfmError as exc:
        return False, f"Last.fm said: {exc}"
    tags = ((data or {}).get("toptags") or {}).get("tag") or []
    if not tags:
        return False, "Last.fm answered but returned no tags. Try again in a minute."
    return True, "Key works. Last.fm will fill in missing genres on your next sync."


def fetch_lastfm_tags(
    session: requests.Session, api_key: str, artist_names: list[str], progress: Progress = _noop,
    out: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    out = {} if out is None else out
    for i, name in enumerate(artist_names, start=1):
        data = _lastfm(session, {
            "method": "artist.gettoptags", "artist": name, "api_key": api_key, "autocorrect": 1,
        })
        if data is not None:
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
    lastfm_problem: str | None = None

    artists: dict[str, str] = {}
    for t in tracks.values():
        for a in t["artists"]:
            if a.get("id"):
                artists[a["id"]] = a["name"]

    missing_artists = [a for a in artists if _needs_lookup(cache, "spotify_genres", a, now)]
    if missing_artists:
        found: dict[str, list[str]] = {}
        try:
            # One-at-a-time lookups cost a request per artist and can earn a day-long rate limit,
            # so only fall back to them when there's no Last.fm key to fill the gaps instead.
            fetch_spotify_genres(sp, missing_artists, progress, found, allow_single=not lastfm_key)
        except NoSpotifyGenres:
            # Spotify isn't giving this app genres. Mark everyone as checked so Last.fm takes over.
            found.update({a: [] for a in missing_artists if a not in found})
            problems.append("Spotify returned no genres for your artists, so Loom stopped asking it "
                            "and relies on Last.fm.")
        except Cancelled:
            _store(cache, "spotify_genres", found, now)
            save_cache(cache, cache_path)
            raise
        except Exception as exc:  # genres are best effort; keep going with BPM
            problems.append(f"Spotify genre lookup stopped early ({_describe(exc)}). "
                            "The rest are picked up on your next sync.")
        _store(cache, "spotify_genres", found, now)
        save_cache(cache, cache_path)

    missing_tracks = [tid for tid in tracks if _needs_lookup(cache, "features", tid, now)]
    if missing_tracks:
        got: dict[str, Any] = {}
        try:
            fetch_audio_features(session, missing_tracks, progress, got)
        except Cancelled:
            _store(cache, "features", got, now)
            save_cache(cache, cache_path)
            raise
        except Exception as exc:
            problems.append(f"Couldn't reach ReccoBeats for BPM data ({_describe(exc)}).")
        _store(cache, "features", got, now)
        save_cache(cache, cache_path)

    no_bpm = [
        tracks[tid] for tid in tracks
        if not (cache["features"].get(tid) or {}).get("tempo") and _needs_lookup(cache, "deezer", tid, now)
    ]
    if no_bpm:
        bpm: dict[str, Any] = {}
        try:
            fetch_deezer_bpm(session, no_bpm, progress, bpm)
        except Cancelled:
            _store(cache, "deezer", bpm, now)
            save_cache(cache, cache_path)
            raise
        except Exception as exc:
            problems.append(f"Couldn't reach Deezer for extra BPM data ({_describe(exc)}).")
        _store(cache, "deezer", bpm, now)
        save_cache(cache, cache_path)

    if lastfm_key:
        need = sorted({
            name for aid, name in artists.items()
            if not cache["spotify_genres"].get(aid)
            and _needs_lookup(cache, "lastfm_tags", name.lower(), now)
        })
        if need:
            tags: dict[str, list[str]] = {}
            try:
                fetch_lastfm_tags(session, lastfm_key, need, progress, tags)
            except Cancelled:
                _store(cache, "lastfm_tags", tags, now)
                save_cache(cache, cache_path)
                raise
            except LastfmError as exc:
                lastfm_problem = f"{exc}. Check the key with Test key in Settings."
            except Exception as exc:
                lastfm_problem = f"couldn't reach Last.fm ({_describe(exc)})."
            _store(cache, "lastfm_tags", tags, now)
            save_cache(cache, cache_path)

    library["artists"] = {}
    for aid, name in artists.items():
        spotify = _clean(cache["spotify_genres"].get(aid) or [])
        lastfm = _clean(cache["lastfm_tags"].get(name.lower()) or [])
        library["artists"][aid] = {
            "name": name,
            "genres": spotify or lastfm,
            "genre_source": "spotify" if spotify else ("lastfm" if lastfm else None),
        }
    for tid, t in tracks.items():
        features = cache["features"].get(tid)
        if features and features.get("tempo"):
            features = {**features, "source": "reccobeats"}
        elif (cache["deezer"].get(tid) or {}).get("tempo"):
            features = {**(features or {}), "tempo": cache["deezer"][tid]["tempo"], "source": "deezer"}
        t["features"] = features or None
    estimate_missing(library)
    library["enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    library["enrich_problems"] = problems
    library["lastfm_used"] = bool(lastfm_key)
    library["lastfm_problem"] = lastfm_problem
    return library


def _describe(exc: Exception) -> str:
    if isinstance(exc, SpotifyException):
        return f"HTTP {exc.http_status}: {exc.msg}".strip()
    return f"{type(exc).__name__}: {exc}"


def estimate_missing(library: dict[str, Any]) -> None:
    """Crude guesses for what no source had, marked so the app can show them as estimates:
    * BPM: the average of the same artist's songs that do have a BPM (needs at least 2)
    * genre: from the names of the playlists the artist is in ("Slow Country" -> country), or else
      the most common genre among artists they share playlists with
    """
    from .genres import family_of  # local import: genres.py is GUI-free but keeps this module light

    tracks, artists = library["tracks"], library.get("artists", {})

    # BPM by artist
    tempos: dict[str, list[float]] = {}
    for t in tracks.values():
        f = t.get("features") or {}
        if f.get("tempo") and not f.get("estimated") and t["artists"]:
            tempos.setdefault(t["artists"][0].get("id"), []).append(f["tempo"])
    for t in tracks.values():
        f = t.get("features") or {}
        if f.get("tempo") or not t["artists"]:
            continue
        known = tempos.get(t["artists"][0].get("id")) or []
        if len(known) >= 2:
            t["features"] = {**f, "tempo": sum(known) / len(known), "estimated": True, "source": "artist average"}

    # Genres from playlists
    missing = {aid for aid, a in artists.items() if not a.get("genres")}
    if not missing:
        return
    in_playlists: dict[str, list[dict[str, Any]]] = {}
    for p in library.get("playlists", []):
        ids = {a.get("id") for e in p["tracks"] if e["id"] in tracks for a in tracks[e["id"]]["artists"]}
        for aid in ids:
            in_playlists.setdefault(aid, []).append({"name": p["name"], "artists": ids})
    for aid in missing:
        lists = in_playlists.get(aid) or []
        by_name = Counter(f for f in (family_of(p["name"]) for p in lists) if f)
        guess: list[str] = []
        if by_name:
            guess = [by_name.most_common(1)[0][0].lower()]
        else:
            near = Counter(
                g for p in lists for other in p["artists"] if other != aid
                for g in (artists.get(other) or {}).get("genres", [])[:2]
                if (artists.get(other) or {}).get("genre_source") != "estimated"
            )
            if near:
                guess = [near.most_common(1)[0][0]]
        if guess:
            artists[aid].update(genres=guess, genre_source="estimated")


def track_genres(library: dict[str, Any], track: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for a in track["artists"]:
        for g in (library.get("artists", {}).get(a.get("id")) or {}).get("genres", []):
            if g not in seen:
                seen.append(g)
    return seen
