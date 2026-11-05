"""Journeys: playlists that move gradually through tempo and energy, then open into an album.

The engine is plain Python (no GUI, no network) so it can be tested directly:
  1. Build a target curve for BPM and energy from the chosen shape.
  2. Walk the curve, picking at each step the unused song that best fits the target while
     staying close to the previous song in genre, key and energy.
  3. Pick an album whose songs sit where the curve ends, to play in full at the end.
"""

from __future__ import annotations

import random
import statistics
from collections import Counter
from typing import Any

from .genres import family_of, primary_group, song_genres

SHAPES = {
    "wind_down": "Wind down",
    "build_up": "Build up",
    "arc": "Arc (up, then down)",
    "steady": "Steady cruise",
}


# ---------------------------------------------------------------- song facts


def tempo(t: dict[str, Any]) -> float | None:
    return (t.get("features") or {}).get("tempo")


def energy(t: dict[str, Any]) -> float | None:
    return (t.get("features") or {}).get("energy")


def camelot(t: dict[str, Any]) -> tuple[int, str] | None:
    """Key as a Camelot wheel position (number 1-12, letter A=minor / B=major)."""
    f = t.get("features") or {}
    key, mode = f.get("key"), f.get("mode")
    if key is None or key < 0 or mode is None:
        return None
    major = int(key) if mode == 1 else (int(key) + 3) % 12  # relative major for minor keys
    number = (major * 7 % 12 + 7) % 12 + 1
    return number, ("B" if mode == 1 else "A")


def key_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    """0 for a perfect harmonic match, up to 1 for a clash; 0.25 when a key is unknown."""
    ca, cb = camelot(a), camelot(b)
    if not ca or not cb:
        return 0.25
    steps = min((ca[0] - cb[0]) % 12, (cb[0] - ca[0]) % 12)
    if ca[1] == cb[1]:
        return {0: 0.0, 1: 0.1}.get(steps, min(1.0, 0.3 + 0.15 * steps))
    return 0.15 if steps == 0 else min(1.0, 0.45 + 0.15 * steps)


def tempo_gap(bpm: float, target: float) -> float:
    """BPM distance, treating half/double time as close (a 75 BPM ballad grooves like 150)."""
    direct = abs(bpm - target)
    folded = min(abs(bpm * 2 - target), abs(bpm / 2 - target)) + 20  # allowed, but only as a last resort
    return min(direct, folded)


# ---------------------------------------------------------------- the curve


def target_curve(shape: str, length: int, start: float, low: float, high: float) -> list[float]:
    """Target value per position, easing smoothly (no sudden jumps)."""
    def ease(x: float) -> float:  # smoothstep
        return x * x * (3 - 2 * x)

    out = []
    for i in range(length):
        x = i / max(length - 1, 1)
        if shape == "wind_down":
            v = start + (low - start) * ease(x)
        elif shape == "build_up":
            v = start + (high - start) * ease(x)
        elif shape == "arc":
            peak = high
            v = start + (peak - start) * ease(x / 0.55) if x <= 0.55 else peak + (low - peak) * ease((x - 0.55) / 0.45)
        else:
            v = start
        out.append(v)
    return out


# ---------------------------------------------------------------- building


def _family(library, t) -> str | None:
    return primary_group(library, t, "broad")


_memo: dict[int, dict[str, tuple[frozenset, str | None]]] = {}


def _facts(library, t) -> tuple[frozenset, str | None]:
    cache = _memo.setdefault(id(library), {})
    if t["id"] not in cache:
        cache[t["id"]] = (frozenset(song_genres(library, t)), _family(library, t))
    return cache[t["id"]]


def genre_distance(library, a, b) -> float:
    (ga, fa), (gb, fb) = _facts(library, a), _facts(library, b)
    if not ga or not gb:
        return 0.6
    if ga & gb:
        return 0.0
    return 0.35 if fa and fa == fb else 1.2


def build(
    library: dict[str, Any],
    entries: list[dict[str, Any]],
    shape: str = "wind_down",
    length: int = 25,
    start_id: str | None = None,
    genre_family: str | None = None,
    end_with_album: bool = True,
    seed: int | None = None,
) -> dict[str, Any]:
    """Build the chain, then pick the closing album; if the album's songs were in the chain, build
    the chain again without them so the journey keeps its full length."""
    result = _build(library, entries, shape, length, start_id, genre_family, seed, exclude=set())
    if not end_with_album or not result["track_ids"]:
        return {**result, "album": None}
    tracks = library["tracks"]
    album = pick_album(library, entries, [tracks[t] for t in result["track_ids"]],
                       result["targets"][-1])
    if album and set(album["known_track_ids"]) & set(result["track_ids"]):
        result = _build(library, entries, shape, length, start_id, genre_family, seed,
                        exclude=set(album["known_track_ids"]) - {start_id})
    return {**result, "album": album}


def _build(
    library: dict[str, Any],
    entries: list[dict[str, Any]],
    shape: str,
    length: int,
    start_id: str | None,
    genre_family: str | None,
    seed: int | None,
    exclude: set[str],
) -> dict[str, Any]:
    """Returns {"track_ids", "targets", "start_bpm", "low", "high", "skipped"}."""
    rng = random.Random(seed)
    _memo.clear()
    tracks = library["tracks"]
    pool = []
    seen = set()
    for e in entries:
        t = tracks.get(e["id"])
        if not t or t["id"] in seen or t["id"] in exclude or not tempo(t):
            continue
        if genre_family and _family(library, t) != genre_family and t["id"] != start_id:
            continue
        seen.add(t["id"])
        pool.append(t)
    skipped = sum(1 for e in entries if e["id"] in tracks and not tempo(tracks[e["id"]]))
    if not pool:
        return {"track_ids": [], "targets": [], "start_bpm": None,
                "low": None, "high": None, "skipped": skipped}

    bpms = sorted(tempo(t) for t in pool)
    q = statistics.quantiles(bpms, n=10) if len(bpms) >= 10 else [bpms[0]] * 9
    low, high = q[0], q[-1]

    if start_id and start_id in tracks and tempo(tracks[start_id]):
        first = tracks[start_id]
    else:
        # A natural opener for the shape: fast for wind-downs, slow for build-ups, mid otherwise.
        aim = {"wind_down": q[7], "build_up": q[1], "arc": q[3]}.get(shape, statistics.median(bpms))
        first = min(pool, key=lambda t: abs(tempo(t) - aim) + rng.random() * 4)
    start = tempo(first)
    low, high = min(low, start), max(high, start)
    length = max(2, min(length, len(pool)))
    targets = target_curve(shape, length, start, low, high)
    e_vals = [energy(t) for t in pool if energy(t) is not None]
    e_low, e_high = (min(e_vals), max(e_vals)) if e_vals else (0.0, 1.0)

    chain = [first]
    used = {first["id"]}
    for pos in range(1, length):
        prev = chain[-1]
        target = targets[pos]
        # energy target follows the BPM curve's position inside the range
        frac = (target - low) / (high - low) if high > low else 0.5
        e_target = e_low + (e_high - e_low) * frac
        recent_artists = Counter(a["name"] for t in chain[-6:] for a in t["artists"][:1])
        scored = []
        for t in pool:
            if t["id"] in used:
                continue
            cost = tempo_gap(tempo(t), target) / 6
            cost += genre_distance(library, prev, t) * 1.5
            cost += key_distance(prev, t) * 0.8
            if energy(t) is not None:
                cost += abs(energy(t) - e_target) * 2
            artist = t["artists"][0]["name"] if t["artists"] else ""
            if prev["artists"] and artist == prev["artists"][0]["name"]:
                cost += 1.5
            cost += 0.6 * recent_artists.get(artist, 0)
            if (t.get("features") or {}).get("estimated"):
                cost += 0.3
            scored.append((cost + rng.random() * 0.4, t))
        if not scored:
            break
        scored.sort(key=lambda c: c[0])
        pick = rng.choice(scored[:2]) if seed is not None and len(scored) > 1 else scored[0]
        chain.append(pick[1])
        used.add(pick[1]["id"])

    return {
        "track_ids": [t["id"] for t in chain],
        "targets": targets[: len(chain)],
        "start_bpm": start, "low": low, "high": high,
        "skipped": skipped,
    }


def pick_album(library, entries, chain, end_bpm: float) -> dict[str, Any] | None:
    """An album from your library whose songs sit near the curve's last BPM and genre."""
    tracks = library["tracks"]
    tail = chain[-4:]
    by_album: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        t = tracks.get(e["id"])
        if t and (t.get("album") or {}).get("id"):
            by_album.setdefault(t["album"]["id"], []).append(t)
    best = None
    for album_id, songs in by_album.items():
        songs = list({t["id"]: t for t in songs}.values())
        known = [tempo(t) for t in songs if tempo(t)]
        if not known:
            continue
        name = songs[0]["album"]["name"]
        if not name or name.lower() == songs[0]["name"].lower() and len(songs) == 1:
            continue  # a single, not an album
        cost = abs(statistics.mean(known) - end_bpm) / 4
        cost += min(genre_distance(library, t, s) for t in tail for s in songs) * 1.5
        cost -= min(len(songs), 5) * 0.25  # prefer albums you clearly like
        if best is None or cost < best[0]:
            best = (cost, album_id, songs)
    if not best:
        return None
    _, album_id, songs = best
    first = songs[0]
    return {
        "id": album_id,
        "name": first["album"]["name"],
        "artist": first["artists"][0]["name"] if first["artists"] else "",
        "known_track_ids": [t["id"] for t in songs],
        "avg_bpm": statistics.mean(tempo(t) for t in songs if tempo(t)),
    }


def family_options(library: dict[str, Any], entries: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts = Counter()
    for e in entries:
        t = library["tracks"].get(e["id"])
        if t and tempo(t):
            f = _family(library, t)
            if f:
                counts[f] += 1
    return counts.most_common()
