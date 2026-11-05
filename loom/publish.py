"""Create (or refresh) playlists on the user's Spotify account."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

PREFIX = "Loom · "


def playlist_name(group_name: str) -> str:
    return f"{PREFIX}{group_name}"


def save_playlist(sp, library: dict[str, Any], name: str, description: str, uris: list[str]) -> str:
    """Create a private playlist, or refill the one Loom made before with this name.
    Returns "created" or "updated"."""
    existing = {
        p["name"]: p["id"]
        for p in library.get("playlists", [])
        if p.get("owned") and p["name"].startswith(PREFIX)
    }
    if name in existing:
        pid = existing[name]
        sp.playlist_change_details(pid, description=description)
        sp.playlist_replace_items(pid, uris[:100])
        result = "updated"
    else:
        pid = sp.current_user_playlist_create(name, public=False, description=description)["id"]
        if uris[:100]:
            sp.playlist_add_items(pid, uris[:100])
        result = "created"
    for start in range(100, len(uris), 100):
        sp.playlist_add_items(pid, uris[start:start + 100])
    return result


def album_uris(sp, album_id: str, fallback: list[str]) -> list[str]:
    """The album's full tracklist from Spotify, or the songs you have from it if that fails."""
    try:
        page = sp.album_tracks(album_id, limit=50)
        uris = []
        while page:
            uris += [t["uri"] for t in page.get("items", []) if t and t.get("uri")]
            page = sp.next(page) if page.get("next") else None
        return uris or fallback
    except Exception:
        return fallback


def publish_journey(sp, library: dict[str, Any], journey: dict[str, Any], name: str,
                    description: str) -> dict[str, Any]:
    uris = [f"spotify:track:{tid}" for tid in journey["track_ids"]]
    album = journey.get("album")
    album_count = 0
    if album:
        extra = album_uris(sp, album["id"], [f"spotify:track:{t}" for t in album["known_track_ids"]])
        extra = [u for u in extra if u not in set(uris)]
        album_count = len(extra)
        uris += extra
    result = save_playlist(sp, library, name, description, uris)
    return {"created": [name] if result == "created" else [],
            "updated": [name] if result == "updated" else [], "songs": len(uris), "album_songs": album_count}


def publish(
    sp,
    library: dict[str, Any],
    groups: list[dict[str, Any]],
    source_label: str,
    progress: Callable[[str, int, int], None] = lambda *a: None,
) -> dict[str, Any]:
    """Make one private playlist per group. A playlist Loom made before (same name, owned by you)
    is refilled instead of duplicated. Playlists Loom didn't make are never touched."""
    user_id = library["user"]["id"]
    created, updated = [], []
    for i, group in enumerate(groups, start=1):
        name = playlist_name(group["name"])
        progress(f"Saving “{name}”", i - 1, len(groups))
        uris = [f"spotify:track:{tid}" for tid in group["track_ids"]]
        description = f"{len(uris)} songs from your {source_label}, sorted by Loom on {date.today():%b %d, %Y}."
        if save_playlist(sp, library, name, description, uris) == "updated":
            updated.append(name)
        else:
            created.append(name)
    progress("Done", len(groups), len(groups))
    return {"created": created, "updated": updated, "user": user_id}
