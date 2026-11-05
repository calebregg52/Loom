from loom import discover, genres
from loom.spotify import RateLimited


def sp_track(i, name, artist, year="1984"):
    return {"id": f"s{i:021d}"[:22], "name": name, "artists": [{"id": f"a-{artist}", "name": artist}],
            "album": {"id": f"al{i}", "name": f"{artist} LP", "release_date": year, "images": []},
            "duration_ms": 240000, "uri": f"spotify:track:s{i}", "external_ids": {"isrc": f"US{i}"}}


CATALOG = [sp_track(i, n, a) for i, (n, a) in enumerate([
    ("Livin' on a Prayer", "Bon Jovi"), ("Don't Stop Believin'", "Journey"),
    ("Sweet Child O' Mine", "Guns N' Roses"), ("Pour Some Sugar on Me", "Def Leppard"),
    ("Jump", "Van Halen"), ("Eye of the Tiger", "Survivor"), ("Back in Black", "AC/DC"),
    ("Here I Go Again", "Whitesnake"), ("Africa", "Toto"), ("Take on Me", "a-ha"),
    ("Every Breath You Take", "The Police"), ("With or Without You", "U2"),
])]


class FakeSp:
    def __init__(self, fail_after=None):
        self.calls = []
        self.fail_after = fail_after

    def search(self, q, type, limit, offset=0):
        assert limit <= 10  # Spotify's cap for new apps
        self.calls.append(q)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise RateLimited(3 * 3600)
        if q.startswith("genre:"):
            assert 'genre:"rock"' in q and "year:1980-1989" in q
            items = CATALOG[6:][offset:offset + limit]  # search knows only some of them
        else:
            title = q.split('track:"')[1].split('"')[0]
            items = [t for t in CATALOG if t["name"].lower() == title.lower()]
            items += [sp_track(99, title + " - 2011 Remaster", "Someone Else")]
        return {"tracks": {"items": items}}


class Resp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeLastfm:
    headers = {}

    def __init__(self):
        self.tags = []

    def get(self, url, params, timeout):
        self.tags.append(params["tag"])
        if params["tag"] != "80s rock":
            return Resp({"tracks": {"track": []}})
        top = [CATALOG[i] for i in (0, 1, 2, 3, 4, 5)] + [CATALOG[8]]
        return Resp({"tracks": {"track": [{"name": t["name"], "artist": {"name": t["artists"][0]["name"]}}
                                          for t in top] + [{"name": "Lost Song", "artist": {"name": "Nobody"}}]}})


def test_norm_matches_versions_and_featured():
    assert discover.norm("Back In Black - 2003 Remaster") == discover.norm("Back in Black")
    assert discover.norm("Jump (Remastered 2015)") == "jump"
    assert discover.norm("The Police") == "police"
    assert discover.lastfm_tags("rock", "1980s") == ["80s rock", "rock 80s"]
    assert discover.spotify_query("rock", "1980s") == 'genre:"rock" year:1980-1989'


def test_find_ranks_lastfm_first_and_skips_owned():
    lib = {"tracks": {"x": {"id": "x", "name": "Jump", "artists": [{"name": "Van Halen"}]}}}
    r = discover.find(FakeSp(), "rock", "1980s", want=10, library=lib, lastfm_key="k", session=FakeLastfm())
    names = [r["tracks"][t]["name"] for t in r["order"]]
    assert names[0] == "Livin' on a Prayer"  # Last.fm's #1
    assert "Jump" not in names  # already in the library
    assert "Africa" in names[:6]  # in both sources, so it ranks up
    assert "Back in Black" in names  # from Spotify search only
    assert len(names) == len(set(names)) and len(names) <= 10
    assert all(r["tracks"][t]["isrc"] for t in r["order"])
    assert r["counts"]["lastfm"] == 8 and not r["problems"]


def test_rate_limit_keeps_what_was_found():
    r = discover.find(FakeSp(fail_after=3), "rock", "1980s", want=10, lastfm_key="k", session=FakeLastfm())
    assert r["order"] and any("wait 3 hr" in p for p in r["problems"])


def test_without_lastfm_uses_spotify_search_alone():
    r = discover.find(FakeSp(), "rock", "1980s", want=5)
    assert [r["tracks"][t]["name"] for t in r["order"]][0] == "Back in Black"


def test_merged_view_keeps_my_songs_separate():
    mine = {"tracks": {"m": {"id": "m"}}, "artists": {}, "liked": [{"id": "m"}], "playlists": []}
    found = {"label": "80s rock", "order": ["d"], "tracks": {"d": {"id": "d"}}, "artists": {"a": {}}}
    view = discover.merged(mine, found)
    assert [e["id"] for e in genres.source_entries(view, "all")] == ["m"]
    assert [e["id"] for e in genres.source_entries(view, "discover")] == ["d"]
    assert [e["id"] for e in genres.source_entries(view, "all+discover")] == ["m", "d"]
    assert "d" not in mine["tracks"]
