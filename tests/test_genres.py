from loom import genres, publish


def lib():
    def t(i, artist, tempo=None):
        return {"id": i, "name": i, "artists": [{"id": artist, "name": artist}],
                "features": {"tempo": tempo} if tempo else None}
    return {
        "user": {"id": "me"},
        "tracks": {"1": t("1", "tyler", 130), "2": t("2", "koe", 150), "3": t("3", "bonobo"),
                   "4": t("4", "nobody"), "5": t("5", "simz")},
        "artists": {
            "tyler": {"genres": ["red dirt", "americana", "folk rock"]},
            "koe": {"genres": ["texas country", "country rock"]},
            "bonobo": {"genres": ["downtempo", "electronica"]},
            "nobody": {"genres": []},
            "simz": {"genres": ["uk hip hop", "rap"]},
        },
        "liked": [{"id": i} for i in "12345"],
        "playlists": [{"id": "p", "name": "Loom · Country", "owned": True, "tracks": [{"id": "1"}]},
                      {"id": "q", "name": "Loom · Electronic", "owned": False, "tracks": []}],
    }


def test_broad_groups():
    groups, unsorted = genres.group_songs(lib(), lib()["liked"], "broad", min_songs=1)
    by = {g["name"]: g for g in groups}
    assert by["Country"]["track_ids"] == ["1", "2"] and by["Country"]["avg_bpm"] == 140
    assert set(by) == {"Country", "Electronic", "Hip Hop"} and unsorted == 1
    assert genres.group_songs(lib(), lib()["liked"], "broad", min_songs=2)[0][0]["name"] == "Country"


def test_exact_groups_and_sources():
    groups, _ = genres.group_songs(lib(), genres.source_entries(lib(), "all"), "exact", min_songs=1)
    assert {g["name"] for g in groups} == {"Red Dirt", "Texas Country", "Downtempo", "Uk Hip Hop"}
    assert genres.source_entries(lib(), "p") == [{"id": "1"}]


class FakeSp:
    def __init__(self):
        self.log = []

    def current_user_playlist_create(self, name, public, description):
        self.log.append(("create", name, public))
        return {"id": "new-" + name}

    def playlist_add_items(self, pid, uris):
        self.log.append(("add", pid, len(uris)))

    def playlist_replace_items(self, pid, uris):
        self.log.append(("replace", pid, len(uris)))

    def playlist_change_details(self, pid, description):
        self.log.append(("details", pid))


def test_publish_reuses_only_loom_playlists_you_own():
    sp = FakeSp()
    groups = [{"name": "Country", "track_ids": [str(i) for i in range(250)]},
              {"name": "Electronic", "track_ids": ["3"]}]
    result = publish.publish(sp, lib(), groups, "Liked Songs")
    assert result["updated"] == ["Loom · Country"] and result["created"] == ["Loom · Electronic"]
    assert ("replace", "p", 100) in sp.log and sp.log.count(("add", "p", 100)) == 1 and ("add", "p", 50) in sp.log
    assert ("create", "Loom · Electronic", False) in sp.log  # private, and the unowned one is left alone
