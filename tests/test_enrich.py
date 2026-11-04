from loom import enrich, library


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.headers = data, status, {}

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        if url == enrich.RECCOBEATS_URL:
            ids = params["ids"].split(",")
            return Resp({"content": [
                {"id": "rb-" + i, "href": f"https://open.spotify.com/track/{i}", "tempo": 120.4 + n,
                 "energy": 0.8, "key": 9, "mode": 0, "valence": 0.3}
                for n, i in enumerate(ids) if not i.startswith("U")  # "U..." songs are unknown
            ]})
        tags = {"Artist B": [{"name": "Shoegaze", "count": 100}, {"name": "seen live", "count": 90},
                             {"name": "2010s", "count": 50}, {"name": "dream pop", "count": 40},
                             {"name": "rare", "count": 2}]}
        return Resp({"toptags": {"tag": tags.get(params["artist"], [])}})


class FakeSpotify:
    def __init__(self):
        self.calls = 0

    def artists(self, ids):
        self.calls += 1
        return {"artists": [{"id": i, "genres": ["indie rock"] if i == "A" else []} for i in ids]}


def sid(n):
    return f"{n}".ljust(22, "x")


def make_library():
    def t(tid, artist):
        return {"id": tid, "name": tid, "artists": [{"id": artist, "name": f"Artist {artist}"}],
                "album": {"name": "", "release_date": ""}, "duration_ms": 1000}
    tracks = {sid("a"): t(sid("a"), "A"), sid("b"): t(sid("b"), "B"), sid("U"): t(sid("U"), "B")}
    return {"tracks": tracks, "playlists": [], "liked": []}


def test_enrich_and_cache(tmp_path):
    lib, sp, session = make_library(), FakeSpotify(), FakeSession()
    cache = tmp_path / "cache.json"
    enrich.enrich(lib, sp, cache, lastfm_key="k", session=session)

    a, b, u = lib["tracks"][sid("a")], lib["tracks"][sid("b")], lib["tracks"][sid("U")]
    assert round(a["features"]["tempo"]) == 120 and b["features"]["energy"] == 0.8
    assert u["features"] is None
    assert enrich.key_label(a["features"]) == "A minor"
    assert lib["artists"]["A"] == {"name": "Artist A", "genres": ["indie rock"], "genre_source": "spotify"}
    assert lib["artists"]["B"]["genres"] == ["shoegaze", "dream pop"]
    assert lib["artists"]["B"]["genre_source"] == "lastfm"
    assert enrich.track_genres(lib, b) == ["shoegaze", "dream pop"]

    s = library.summary(lib)
    assert s["genres"] == 3 and s["top_genres"][0] == ("dream pop", 2)
    assert abs(s["bpm_coverage"] - 2 / 3) < 1e-9

    # Second run uses the cache: no new Spotify or HTTP calls.
    before = len(session.calls)
    enrich.enrich(make_library(), sp, cache, lastfm_key="k", session=session)
    assert sp.calls == 1 and len(session.calls) == before


def test_failed_reccobeats_batch_is_retried_next_time(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)

    class Down(FakeSession):
        def get(self, url, params, timeout):
            return Resp({}, status=503)

    lib = make_library()
    enrich.enrich(lib, FakeSpotify(), tmp_path / "c.json", session=Down())
    assert all(t["features"] is None for t in lib["tracks"].values())
    assert enrich.load_cache(tmp_path / "c.json")["features"] == {}


def test_genres_fall_back_to_single_artist_lookups(tmp_path):
    from spotipy.exceptions import SpotifyException

    class NoBulk(FakeSpotify):
        def artists(self, ids):
            raise SpotifyException(403, -1, "Forbidden")

        def artist(self, aid):
            return {"id": aid, "genres": ["outlaw country"]}

    lib = make_library()
    enrich.enrich(lib, NoBulk(), tmp_path / "c.json", session=FakeSession())
    assert lib["artists"]["A"]["genres"] == ["outlaw country"]
    assert lib["enrich_problems"] == []


def test_refused_genres_are_reported(tmp_path):
    from spotipy.exceptions import SpotifyException

    class Refused(FakeSpotify):
        def artists(self, ids):
            raise SpotifyException(403, -1, "Forbidden")

        def artist(self, aid):
            raise SpotifyException(403, -1, "Forbidden")

    lib = make_library()
    enrich.enrich(lib, Refused(), tmp_path / "c.json", session=FakeSession())
    assert "HTTP 403" in lib["enrich_problems"][0]
    assert round(lib["tracks"][sid("a")]["features"]["tempo"]) == 120  # BPM still filled in


def test_empty_results_are_retried_later(tmp_path, monkeypatch):
    cache, session = tmp_path / "c.json", FakeSession()
    enrich.enrich(make_library(), FakeSpotify(), cache, session=session)
    asked = lambda: sum(sid("U") in p.get("ids", "") for _, p in session.calls)
    assert asked() == 1
    enrich.enrich(make_library(), FakeSpotify(), cache, session=session)
    assert asked() == 1  # too soon to ask again
    real = enrich.time.time
    monkeypatch.setattr(enrich.time, "time", lambda: real() + enrich.RETRY_EMPTY_AFTER + 60)
    enrich.enrich(make_library(), FakeSpotify(), cache, session=session)
    assert asked() == 2
