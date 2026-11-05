from spotipy.exceptions import SpotifyException

from loom import library


def track(i, artist="A"):
    return {
        "id": f"t{i}", "name": f"Song {i}", "type": "track", "duration_ms": 180000,
        "artists": [{"id": artist, "name": f"Artist {artist}"}],
        "album": {"id": "al", "name": "Album", "release_date": "2020-01-01", "images": []},
    }


class FakeSpotify:
    """Two pages of liked songs, one readable playlist and one blocked one."""

    def current_user(self):
        return {"id": "me", "display_name": "Caleb"}

    def current_user_saved_tracks(self, limit):
        return {"total": 3, "items": [{"track": track(1), "added_at": "2024-01-01T00:00:00Z"},
                                      {"track": track(2)}], "next": "page2"}

    def next(self, page):
        if page.get("next") == "page2":
            return {"items": [{"track": track(3, "B")}], "next": None}
        return None

    def current_user_playlists(self, limit):
        return {"items": [{"id": "p1", "name": "Mine", "owner": {"id": "me"}},
                          {"id": "p2", "name": "Editorial", "owner": {"id": "spotify"}}], "next": None}

    def playlist_items(self, pid, limit, additional_types):
        if pid == "p2":
            raise SpotifyException(403, -1, "Forbidden")
        return {"items": [
            {"track": track(1)},
            {"track": {"id": None}},  # removed song
            {"track": {**track(9), "is_local": True}},
            {"track": {**track(8), "type": "episode"}},
            {"track": track(4, "B")},
        ], "next": None}


def test_fetch_library(tmp_path):
    seen = []
    data = library.fetch_library(FakeSpotify(), lambda *a: seen.append(a))
    assert [e["id"] for e in data["liked"]] == ["t1", "t2", "t3"]
    assert [p["name"] for p in data["playlists"]] == ["Mine"]
    assert [e["id"] for e in data["playlists"][0]["tracks"]] == ["t1", "t4"]
    assert data["playlists"][0]["owned"] is True
    assert data["skipped_playlists"] == ["Editorial"]
    assert set(data["tracks"]) == {"t1", "t2", "t3", "t4"}
    assert seen[-1][0] == "Playlists done"

    path = tmp_path / "library.json"
    library.save(data, path)
    assert library.load(path) == data

    s = library.summary(data)
    assert s["tracks"] == 4 and s["artists"] == 2 and s["top_artists"][0] == ("Artist A", 2)


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from loom import config

    assert config.load_client_id() is None
    (tmp_path / "secret.yaml").write_text("SPOTIFY_CLIENT_ID: abc123\nSPOTIFY_CLIENT_SECRET: x\n")
    assert config.load_client_id() == "abc123"
    assert "abc123" in config.settings_path().read_text()
