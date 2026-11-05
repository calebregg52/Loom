"""Create (or refresh) playlists on the user's Spotify account."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

PREFIX = "Loom · "


def playlist_name(group_name: str) -> str:
    return f"{PREFIX}{group_name}"


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
    existing = {
        p["name"]: p["id"]
        for p in library.get("playlists", [])
        if p.get("owned") and p["name"].startswith(PREFIX)
    }
    created, updated = [], []
    for i, group in enumerate(groups, start=1):
        name = playlist_name(group["name"])
        progress(f"Saving “{name}”", i - 1, len(groups))
        uris = [f"spotify:track:{tid}" for tid in group["track_ids"]]
        description = f"{len(uris)} songs from your {source_label}, sorted by Loom on {date.today():%b %d, %Y}."
        if name in existing:
            pid = existing[name]
            sp.playlist_change_details(pid, description=description)
            sp.playlist_replace_items(pid, uris[:100])
            updated.append(name)
        else:
            pid = sp.current_user_playlist_create(name, public=False, description=description)["id"]
            if uris[:100]:
                sp.playlist_add_items(pid, uris[:100])
            created.append(name)
        for start in range(100, len(uris), 100):
            sp.playlist_add_items(pid, uris[start:start + 100])
    progress("Done", len(groups), len(groups))
    return {"created": created, "updated": updated, "user": user_id}
