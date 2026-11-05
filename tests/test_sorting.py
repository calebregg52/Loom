from loom import sorting


def lib():
    def t(i, artist, bpm=None, energy=None, key=None, mode=None, year="2020", g=()):
        f = None
        if bpm or energy is not None or key is not None:
            f = {"tempo": bpm, "energy": energy, "key": key, "mode": mode}
        return {"id": i, "name": i, "artists": [{"id": artist, "name": artist}],
                "album": {"release_date": year}, "features": f}
    tracks = {
        "a": t("a", "Zach", 140, 0.9, 0, 1, "2019"),
        "b": t("b", "hardy", 90, 0.2, 7, 1, "2023"),
        "c": t("c", "Kanye", 120, 0.5, 9, 0, "2007"),
        "d": t("d", "Abba", None, None, None, None, ""),
    }
    artists = {"Zach": {"genres": ["country"]}, "hardy": {"genres": ["country"]},
               "Kanye": {"genres": ["hip hop"]}, "Abba": {"genres": ["pop"]}}
    return {"tracks": tracks, "artists": artists}


E = [{"id": x} for x in "abcd"]


def order(mode):
    return [e["id"] for e in sorting.sort_entries(lib(), E, mode)]


def test_sorts():
    assert order("original") == list("abcd")
    assert order("bpm_up") == ["b", "c", "a", "d"]  # unknown BPM last
    assert order("bpm_down") == ["a", "c", "b", "d"]
    assert order("energy_down") == ["a", "c", "b", "d"]
    assert order("year_up") == ["c", "a", "b", "d"]
    assert order("artist") == ["d", "b", "c", "a"]  # case-insensitive
    assert order("genre_bpm") == ["b", "a", "c", "d"]  # Country (slow, fast), Hip Hop, Pop
    assert order("key") == ["c", "a", "b", "d"]  # 8A (A minor), 8B (C major), 9B (G major), unknown


def test_save_sorted_records_new_playlist():
    from loom import publish

    class Sp:
        def current_user_playlist_create(self, name, public, description):
            assert public is False and "by BPM" in description
            return {"id": "p9"}

        def playlist_add_items(self, pid, uris):
            self.uris = uris

    library = {"playlists": [], "user": {"id": "me"}}
    r = publish.save_sorted(Sp(), library, "Loom · Liked Songs · by BPM", ["b", "c", "a"], "by BPM")
    assert r["created"] == ["Loom · Liked Songs · by BPM"]
    assert r["saved"] == [{"id": "p9", "name": "Loom · Liked Songs · by BPM", "track_ids": ["b", "c", "a"]}]
