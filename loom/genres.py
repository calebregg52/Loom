"""Group songs into genre buckets."""

from __future__ import annotations

from collections import Counter
from typing import Any

# Checked in order: the first family whose keyword appears in a genre name wins for that genre.
FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("Metal", ("metal", "metalcore", "deathcore", "djent")),
    ("Punk", ("punk", "hardcore")),
    ("Hip Hop", ("hip hop", "hip-hop", "rap", "trap", "drill", "grime", "boom bap")),
    ("R&B & Soul", ("r&b", "rnb", "soul", "funk", "motown", "new jack swing")),
    ("Jazz", ("jazz", "bebop", "swing", "bossa nova")),
    ("Blues", ("blues",)),
    ("Country", ("country", "americana", "bluegrass", "red dirt", "outlaw", "honky", "texas", "nashville")),
    ("Folk", ("folk", "singer-songwriter", "acoustic")),
    ("Reggae", ("reggae", "dancehall", "ska", "dub ")),
    ("Latin", ("latin", "reggaeton", "salsa", "cumbia", "bachata", "corrido", "mexican", "banda",
               "sertanejo", "urbano", "flamenco", "tango")),
    ("Electronic", ("house", "techno", "edm", "electro", "dubstep", "trance", "drum and bass", "dnb",
                    "garage", "ambient", "downtempo", "synthwave", "disco", "idm", "breakbeat", "dance")),
    ("Indie & Alternative", ("indie", "alternative", "shoegaze", "dream pop", "bedroom pop", "lo-fi", "emo")),
    ("Rock", ("rock", "grunge", "psychedelic")),
    ("Christian & Gospel", ("christian", "worship", "gospel", "ccm")),
    ("Classical & Soundtrack", ("classical", "orchestra", "opera", "soundtrack", "score", "baroque", "piano")),
    ("Pop", ("pop",)),
]


def family_of(genre: str) -> str | None:
    g = f" {genre.lower()} "
    for family, keywords in FAMILIES:
        if any(k in g for k in keywords):
            return family
    return None


def song_genres(library: dict[str, Any], track: dict[str, Any]) -> list[str]:
    artists = library.get("artists", {})
    out: list[str] = []
    for a in track["artists"]:
        for g in (artists.get(a.get("id")) or {}).get("genres", []):
            if g not in out:
                out.append(g)
    return out


def primary_group(library: dict[str, Any], track: dict[str, Any], mode: str) -> str | None:
    """mode "broad": the family most of the song's genres fall into. mode "exact": its first genre."""
    genres = song_genres(library, track)
    if not genres:
        return None
    if mode == "exact":
        return genres[0]
    votes = Counter(f for f in (family_of(g) for g in genres) if f)
    if not votes:
        return "Other"
    best = max(votes.values())
    for g in genres:  # ties go to the song's first-listed genre
        f = family_of(g)
        if f and votes[f] == best:
            return f
    return None


def source_entries(library: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """source: "liked", "all", or a playlist id."""
    if source == "liked":
        return library.get("liked", [])
    if source == "all":
        seen, out = set(), []
        for e in library.get("liked", []) + [e for p in library.get("playlists", []) for e in p["tracks"]]:
            if e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
        return out
    for p in library.get("playlists", []):
        if p["id"] == source:
            return p["tracks"]
    return []


def group_songs(
    library: dict[str, Any], entries: list[dict[str, Any]], mode: str = "broad", min_songs: int = 5
) -> tuple[list[dict[str, Any]], int]:
    """Returns (groups biggest first, number of songs with no genre). Each group has
    name, track_ids (in source order), top_artists and avg_bpm."""
    tracks = library["tracks"]
    buckets: dict[str, list[str]] = {}
    unsorted = 0
    for e in entries:
        t = tracks.get(e["id"])
        if not t:
            continue
        g = primary_group(library, t, mode)
        if g is None:
            unsorted += 1
            continue
        buckets.setdefault(g, []).append(t["id"])
    groups = []
    for name, ids in buckets.items():
        if len(ids) < min_songs:
            continue
        artists = Counter(tracks[i]["artists"][0]["name"] for i in ids if tracks[i]["artists"])
        tempos = [tracks[i]["features"]["tempo"] for i in ids if (tracks[i].get("features") or {}).get("tempo")]
        groups.append({
            "name": name.title() if mode == "exact" else name,
            "track_ids": ids,
            "top_artists": [a for a, _ in artists.most_common(3)],
            "avg_bpm": sum(tempos) / len(tempos) if tempos else None,
        })
    groups.sort(key=lambda g: (-len(g["track_ids"]), g["name"]))
    return groups, unsorted
