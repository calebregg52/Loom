"""Download a snapshot of the user's library and keep it as JSON on disk."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from spotipy.exceptions import SpotifyException

Progress = Callable[[str, int, int], None]  # message, done, total


def _noop(message: str, done: int, total: int) -> None:
    pass


def _track_record(track: dict[str, Any]) -> dict[str, Any]:
    album = track.get("album") or {}
    images = album.get("images") or []
    return {
        "id": track["id"],
        "name": track.get("name") or "",
        "artists": [
            {"id": a.get("id"), "name": a.get("name") or ""} for a in track.get("artists") or []
        ],
        "album": {
            "id": album.get("id"),
            "name": album.get("name") or "",
            "release_date": album.get("release_date") or "",
            "image": images[-1]["url"] if images else None,
        },
        "duration_ms": track.get("duration_ms") or 0,
        "explicit": bool(track.get("explicit")),
        "popularity": track.get("popularity"),
        "uri": track.get("uri"),
    }


def _item_track(item: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the track out of a playlist/saved item, skipping local files, episodes and removed songs."""
    track = item.get("track") or item.get("item")
    if not isinstance(track, dict) or not track.get("id"):
        return None
    if track.get("type", "track") != "track" or track.get("is_local"):
        return None
    return track


def _pages(sp, first: dict[str, Any]):
    page = first
    while page:
        yield page
        page = sp.next(page) if page.get("next") else None


def fetch_library(sp, progress: Progress = _noop) -> dict[str, Any]:
    me = sp.current_user()
    tracks: dict[str, dict[str, Any]] = {}

    def remember(item: dict[str, Any]) -> tuple[str, str | None] | None:
        track = _item_track(item)
        if track is None:
            return None
        tracks.setdefault(track["id"], _track_record(track))
        return track["id"], item.get("added_at")

    # Liked songs
    liked: list[dict[str, Any]] = []
    first = sp.current_user_saved_tracks(limit=50)
    total = first.get("total", 0)
    for page in _pages(sp, first):
        for item in page.get("items", []):
            got = remember(item)
            if got:
                liked.append({"id": got[0], "added_at": got[1]})
        progress("Reading liked songs", len(liked), total)

    # Playlists
    playlist_meta: list[dict[str, Any]] = []
    for page in _pages(sp, sp.current_user_playlists(limit=50)):
        playlist_meta.extend(p for p in page.get("items", []) if p)

    playlists: list[dict[str, Any]] = []
    skipped: list[str] = []
    for index, meta in enumerate(playlist_meta, start=1):
        name = meta.get("name") or "Untitled"
        progress(f"Reading playlist “{name}”", index, len(playlist_meta))
        entries: list[dict[str, Any]] = []
        try:
            for page in _pages(sp, sp.playlist_items(meta["id"], limit=100, additional_types=("track",))):
                for item in page.get("items", []):
                    got = remember(item)
                    if got:
                        entries.append({"id": got[0], "added_at": got[1]})
        except SpotifyException:
            # Spotify blocks third-party apps from some playlists (e.g. its own editorial ones).
            skipped.append(name)
            continue
        owner = meta.get("owner") or {}
        images = meta.get("images") or []
        playlists.append(
            {
                "id": meta["id"],
                "name": name,
                "description": meta.get("description") or "",
                "owner": owner.get("display_name") or owner.get("id") or "",
                "owned": owner.get("id") == me.get("id"),
                "image": images[0]["url"] if images else None,
                "tracks": entries,
            }
        )

    progress("Playlists done", 1, 1)
    return {
        "version": 1,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user": {"id": me.get("id"), "name": me.get("display_name") or me.get("id")},
        "tracks": tracks,
        "liked": liked,
        "playlists": playlists,
        "skipped_playlists": skipped,
    }


def save(library: dict[str, Any], path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(library, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summary(library: dict[str, Any]) -> dict[str, Any]:
    tracks = library.get("tracks", {})
    artist_counts: dict[str, int] = {}
    for t in tracks.values():
        for a in t["artists"]:
            artist_counts[a["name"]] = artist_counts.get(a["name"], 0) + 1
    top = sorted(artist_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    artist_info = library.get("artists", {})
    genre_counts: dict[str, int] = {}
    for t in tracks.values():
        genres = set()
        for a in t["artists"]:
            genres.update((artist_info.get(a.get("id")) or {}).get("genres", []))
        for g in genres:
            genre_counts[g] = genre_counts.get(g, 0) + 1
    tempos = [t["features"]["tempo"] for t in tracks.values() if (t.get("features") or {}).get("tempo")]
    return {
        "top_genres": sorted(genre_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10],
        "genres": len(genre_counts),
        "avg_bpm": sum(tempos) / len(tempos) if tempos else None,
        "bpm_coverage": len(tempos) / len(tracks) if tracks else 0.0,
        "playlists": len(library.get("playlists", [])),
        "liked": len(library.get("liked", [])),
        "tracks": len(tracks),
        "artists": len(artist_counts),
        "hours": sum(t["duration_ms"] for t in tracks.values()) / 3_600_000,
        "top_artists": top,
    }
