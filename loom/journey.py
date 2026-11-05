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

# Each shape is a list of control points (x = position 0..1, y = 0 slowest .. 1 fastest within
# the BPM range of the songs being used). The curve eases smoothly between points.
PRESETS: dict[str, dict[str, Any]] = {
    "wind_down": {"label": "Wind down", "points": [(0, 0.85), (1, 0.05)]},
    "build_up": {"label": "Build up", "points": [(0, 0.1), (1, 0.95)]},
    "arc": {"label": "Arc (up, then down)", "points": [(0, 0.3), (0.55, 1.0), (1, 0.05)]},
    "steady": {"label": "Steady cruise", "points": [(0, 0.5), (1, 0.5)]},
    "valley": {"label": "Valley (down, then back up)", "points": [(0, 0.8), (0.5, 0.05), (1, 0.85)]},
    "wave": {"label": "Wave", "points": [(0, 0.5), (0.17, 0.9), (0.42, 0.15), (0.67, 0.9), (0.9, 0.2), (1, 0.4)]},
    "rollercoaster": {"label": "Rollercoaster", "points": [(0, 0.3), (0.15, 0.95), (0.3, 0.2), (0.5, 1.0),
                                                           (0.65, 0.15), (0.85, 0.9), (1, 0.1)]},
    "slow_burn": {"label": "Slow burn (late climb)", "points": [(0, 0.1), (0.6, 0.25), (1, 1.0)]},
    "sunrise": {"label": "Sunrise (climb, then cruise)", "points": [(0, 0.05), (0.4, 0.7), (1, 0.75)]},
    "sunset": {"label": "Sunset (cruise, then fade)", "points": [(0, 0.7), (0.55, 0.65), (1, 0.0)]},
    "workout": {"label": "Workout (warm-up, peak, cool-down)",
                "points": [(0, 0.3), (0.15, 0.8), (0.75, 0.95), (1, 0.15)]},
    "intervals": {"label": "Intervals (fast / slow)",
                  "points": [(0, 0.2), (0.12, 0.95), (0.25, 0.2), (0.37, 0.95), (0.5, 0.2),
                             (0.62, 0.95), (0.75, 0.2), (0.87, 0.95), (1, 0.15)]},
    "drop": {"label": "Big drop", "points": [(0, 0.9), (0.45, 0.95), (0.55, 0.1), (1, 0.2)]},
    "late_night": {"label": "Late night drive", "points": [(0, 0.55), (0.3, 0.6), (0.7, 0.4), (1, 0.25)]},
    "party": {"label": "Party (build, hold, last song)", "points": [(0, 0.4), (0.3, 0.9), (0.85, 1.0), (1, 0.3)]},
}
SHAPES = {k: v["label"] for k, v in PRESETS.items()}  # key -> label, for menus


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


def clean_points(points) -> list[tuple[float, float]]:
    """Sort by position, clamp to 0..1, and make sure the curve spans the whole journey."""
    pts = sorted((min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)) for x, y in points)
    if not pts:
        return [(0.0, 0.5), (1.0, 0.5)]
    if pts[0][0] > 0:
        pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < 1:
        pts.append((1.0, pts[-1][1]))
    return pts


def curve_at(points, x: float) -> float:
    """Height 0..1 of the curve at position x, easing smoothly between control points."""
    pts = clean_points(points)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1 or (x1, y1) == pts[-1]:
            if x1 <= x0:
                return y1
            u = min(max((x - x0) / (x1 - x0), 0.0), 1.0)
            return y0 + (y1 - y0) * u * u * (3 - 2 * u)  # smoothstep
    return pts[-1][1]


def target_curve(points, length: int, low: float, high: float) -> list[float]:
    """Target BPM per position."""
    return [low + (high - low) * curve_at(points, i / max(length - 1, 1)) for i in range(length)]


def shape_points(shape) -> list[tuple[float, float]]:
    if isinstance(shape, (list, tuple)):
        return clean_points(shape)
    return clean_points(PRESETS.get(shape, PRESETS["steady"])["points"])


def bpm_range(library: dict[str, Any], entries: list[dict[str, Any]], genre_family: str | None = None):
    """The 10th to 90th percentile BPM of usable songs: the curve's 0 and 1."""
    tracks = library["tracks"]
    bpms = sorted(
        tempo(tracks[e["id"]]) for e in entries
        if e["id"] in tracks and tempo(tracks[e["id"]])
        and (not genre_family or _family(library, tracks[e["id"]]) == genre_family)
    )
    if not bpms:
        return None
    if len(bpms) < 10:
        return bpms[0], bpms[-1]
    q = statistics.quantiles(bpms, n=10)
    return q[0], q[-1]


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
    shape: str | list = "wind_down",  # a preset key, or your own [(x, y), ...] points
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
    shape: str | list,
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
    if len(bpms) >= 10:
        q = statistics.quantiles(bpms, n=10)
        low, high = q[0], q[-1]
    else:
        low, high = bpms[0], bpms[-1]
    length = max(2, min(length, len(pool)))
    targets = target_curve(shape_points(shape), length, low, high)

    if start_id and start_id in tracks and tempo(tracks[start_id]):
        first = tracks[start_id]
    else:
        first = min(pool, key=lambda t: abs(tempo(t) - targets[0]) + rng.random() * 4)
    start = tempo(first)
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
