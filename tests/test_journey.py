import random

from loom import journey, publish


def make_library(n=300, seed=1):
    rng = random.Random(seed)
    artists = {f"c{i}": ["country", "red dirt"] for i in range(12)}
    artists.update({f"h{i}": ["hip hop", "trap"] for i in range(8)})
    artists.update({f"r{i}": ["classic rock"] for i in range(6)})
    tracks, liked = {}, []
    names = list(artists)
    for i in range(n):
        a = rng.choice(names)
        album = f"{a}-album{rng.randint(0, 2)}"
        tracks[f"t{i}"] = {
            "id": f"t{i}", "name": f"Song {i}", "artists": [{"id": a, "name": a}],
            "album": {"id": album, "name": album, "release_date": "2020"}, "duration_ms": 200000,
            "features": {"tempo": rng.uniform(70, 170), "energy": rng.random(),
                         "key": rng.randint(0, 11), "mode": rng.randint(0, 1)} if rng.random() > 0.1 else None,
        }
        liked.append({"id": f"t{i}"})
    lib = {"user": {"id": "me"}, "tracks": tracks, "liked": liked, "playlists": [],
           "artists": {a: {"name": a, "genres": g, "genre_source": "lastfm"} for a, g in artists.items()}}
    return lib


def test_wind_down_goes_down_smoothly():
    lib = make_library()
    j = journey.build(lib, lib["liked"], "wind_down", 25, end_with_album=False)
    bpms = [lib["tracks"][t]["features"]["tempo"] for t in j["track_ids"]]
    assert len(bpms) == 25 and len(set(j["track_ids"])) == 25
    assert sum(bpms[:5]) / 5 - sum(bpms[-5:]) / 5 > 30  # clearly slower by the end
    assert j["targets"] == sorted(j["targets"], reverse=True)
    assert j["skipped"] > 0  # songs without BPM are left out, and counted


def test_build_up_and_arc_shapes():
    lib = make_library()
    up = journey.build(lib, lib["liked"], "build_up", 20, end_with_album=False)
    b = [lib["tracks"][t]["features"]["tempo"] for t in up["track_ids"]]
    assert sum(b[-5:]) / 5 - sum(b[:5]) / 5 > 30
    arc = journey.build(lib, lib["liked"], "arc", 21, end_with_album=False)
    t = arc["targets"]
    peak = t.index(max(t))
    assert 0 < peak < len(t) - 1 and t[0] < t[peak] > t[-1]


def test_transitions_mostly_stay_in_genre():
    lib = make_library()
    j = journey.build(lib, lib["liked"], "steady", 20, end_with_album=False)
    fams = [journey._family(lib, lib["tracks"][t]) for t in j["track_ids"]]
    changes = sum(1 for a, b in zip(fams, fams[1:]) if a != b)
    assert changes <= 4


def test_genre_filter_and_start_song():
    lib = make_library()
    start = next(t for t in lib["tracks"].values() if t["features"] and t["artists"][0]["id"].startswith("c"))
    j = journey.build(lib, lib["liked"], "wind_down", 15, start_id=start["id"], genre_family="Country",
                      end_with_album=False)
    assert j["track_ids"][0] == start["id"]
    assert all(lib["tracks"][t]["artists"][0]["id"].startswith("c") for t in j["track_ids"])


def test_ends_with_an_album_near_the_final_bpm():
    lib = make_library()
    j = journey.build(lib, lib["liked"], "wind_down", 20, end_with_album=True)
    album = j["album"]
    assert album and album["known_track_ids"]
    assert not set(album["known_track_ids"]) & set(j["track_ids"])  # no song twice
    assert abs(album["avg_bpm"] - j["targets"][-1]) < 40


def test_seed_gives_different_but_valid_variations():
    lib = make_library()
    a = journey.build(lib, lib["liked"], "wind_down", 20, seed=1, end_with_album=False)["track_ids"]
    b = journey.build(lib, lib["liked"], "wind_down", 20, seed=2, end_with_album=False)["track_ids"]
    assert a != b and len(a) == len(b) == 20


def test_camelot_and_half_time():
    assert journey.camelot({"features": {"key": 0, "mode": 1}}) == (8, "B")   # C major
    assert journey.camelot({"features": {"key": 9, "mode": 0}}) == (8, "A")   # A minor
    c, g = {"features": {"key": 0, "mode": 1}}, {"features": {"key": 7, "mode": 1}}
    assert journey.key_distance(c, g) == 0.1
    assert journey.tempo_gap(75, 150) < journey.tempo_gap(110, 150)


class FakeSp:
    def __init__(self):
        self.added = []

    def album_tracks(self, album_id, limit):
        return {"items": [{"uri": f"spotify:track:a{i}"} for i in range(3)] + [{"uri": "spotify:track:t1"}],
                "next": None}

    def current_user_playlist_create(self, name, public, description):
        assert public is False
        return {"id": "new"}

    def playlist_add_items(self, pid, uris):
        self.added += uris


def test_publish_journey_appends_full_album_without_duplicates():
    sp = FakeSp()
    lib = {"playlists": [], "user": {"id": "me"}}
    j = {"track_ids": ["t1", "t2"], "album": {"id": "al", "known_track_ids": ["a0"]}}
    r = publish.publish_journey(sp, lib, j, "Loom · Journey", "desc")
    assert sp.added == ["spotify:track:t1", "spotify:track:t2", "spotify:track:a0",
                        "spotify:track:a1", "spotify:track:a2"]
    assert r["created"] == ["Loom · Journey"] and r["album_songs"] == 3


def test_custom_points_are_followed():
    lib = make_library(600)
    pts = [(0, 0.1), (0.5, 0.9), (1, 0.1)]
    j = journey.build(lib, lib["liked"], pts, 31, end_with_album=False)
    t = j["targets"]
    assert t[15] == max(t) and t[0] < t[15] > t[-1]
    bpms = [lib["tracks"][i]["features"]["tempo"] for i in j["track_ids"]]
    assert sum(abs(b - x) for b, x in zip(bpms, t)) / len(t) < 12  # songs track the drawn curve


def test_curve_helpers():
    assert journey.clean_points([(0.5, 2), (0.2, -1)]) == [(0.0, 0.0), (0.2, 0.0), (0.5, 1.0), (1.0, 1.0)]
    assert journey.curve_at([(0, 0), (1, 1)], 0.5) == 0.5
    assert journey.curve_at([(0, 0.2), (0.5, 0.8), (1, 0.2)], 0.5) == 0.8


def test_every_preset_builds():
    lib = make_library()
    for key in journey.PRESETS:
        j = journey.build(lib, lib["liked"], key, 20, end_with_album=False)
        assert len(j["track_ids"]) == 20, key


def test_energy_moves_smoothly_between_songs():
    lib = make_library(600, seed=3)
    for shape in ("build_up", "wind_down", "steady"):
        j = journey.build(lib, lib["liked"], shape, 25, end_with_album=False)
        e = [lib["tracks"][t]["features"]["energy"] for t in j["track_ids"]]
        jumps = [abs(a - b) for a, b in zip(e, e[1:])]
        assert max(jumps) < 0.35, (shape, max(jumps))
        assert sum(jumps) / len(jumps) < 0.15


# ---------------------------------------------------------------- wave-fit planner

from loom import wavefit  # noqa: E402


def _off(lib, j):
    return sum(d["off_pct"] for d in j["details"]) / len(j["details"])


def test_planner_beats_greedy_on_the_same_waves():
    lib = make_library(600, seed=5)
    pool = [t for t in lib["tracks"].values() if journey.tempo(t)]
    wins = 0
    for shape in ("rollercoaster", "intervals", "arc", "drop"):
        pts = journey.shape_points(shape)
        ref = wavefit.Planner(lib, pool, pts, 25)
        g = journey.build_greedy(lib, lib["liked"], shape, 25, end_with_album=False)
        w = journey.build(lib, lib["liked"], shape, 25, end_with_album=False)
        by_id = {s.id: s for s in ref.songs}
        gs = ref.score([by_id[t] for t in g["track_ids"]])
        ws = ref.score([by_id[t] for t in w["track_ids"]])
        wins += ws < gs
        assert len(w["track_ids"]) == 25 and len(set(w["track_ids"])) == 25
        assert _off(lib, w) < 6
    assert wins == 4


def test_scale_bends_toward_the_library_and_round_trips():
    bpms = [90 + i % 10 for i in range(150)] + [100 + i * 1.2 for i in range(50)]
    sc = wavefit.Scale(bpms)
    ys = [k / 20 for k in range(21)]
    vals = [sc.to_bpm(y) for y in ys]
    assert vals == sorted(vals)
    assert all(abs(sc.to_y(sc.to_bpm(y)) - y) < 1e-6 for y in ys)
    # most songs sit at 90-99, so the middle of the wave lands lower than a straight line would put it
    assert sc.to_bpm(0.5) < (sc.low + sc.high) / 2


def test_songs_sit_on_the_wave_by_play_time():
    lib = make_library()
    j = journey.build(lib, lib["liked"], "build_up", 12, end_with_album=False)
    xs = j["xs"]
    assert xs[0] == 0 and abs(xs[-1] - 1) < 1e-9 and xs == sorted(xs)
    durs = [lib["tracks"][t]["duration_ms"] for t in j["track_ids"]]
    assert len(set(durs)) == 1 and all(abs((b - a) - 1 / 11) < 1e-9 for a, b in zip(xs, xs[1:]))


def test_start_song_is_kept_and_album_is_planned_first():
    lib = make_library(400, seed=2)
    start = next(t for t in lib["tracks"].values() if journey.tempo(t))["id"]
    j = journey.build(lib, lib["liked"], "wind_down", 20, start_id=start, seed=3)
    assert j["track_ids"][0] == start
    assert j["album"] and not set(j["album"]["known_track_ids"]) & set(j["track_ids"][1:])


def test_priorities_trade_wave_for_flow():
    lib = make_library(600, seed=8)
    tight = journey.build(lib, lib["liked"], "intervals", 25, end_with_album=False, priority="tight")
    smooth = journey.build(lib, lib["liked"], "intervals", 25, end_with_album=False, priority="smooth")
    assert _off(lib, tight) <= _off(lib, smooth)
    ej = lambda j: max(d["energy_step"] or 0 for d in j["details"][1:])  # noqa: E731
    assert ej(smooth) <= ej(tight) + 0.02


def test_thin_spots_fit_score_and_reasons():
    lib = make_library(120, seed=4)
    for t in lib["tracks"].values():  # nothing above 120 BPM
        if t["features"]:
            t["features"]["tempo"] = 70 + (hash(t["id"]) % 50)
    j = journey.build(lib, lib["liked"], [(0, 0.5), (0.5, 3), (1, 0.5)], 15, end_with_album=False)
    assert 0 <= j["fit_score"] <= 100
    tip = wavefit.why(j["details"][1], "Song 1")
    assert "BPM here" in tip and "Key:" in tip
    sc = j["scale"]
    j2 = wavefit.Planner(lib, [t for t in lib["tracks"].values() if journey.tempo(t)],
                         [(0, 0.5), (0.5, 1.0), (1, 0.5)], 15)
    j2.scale = wavefit.Scale([60, 200] * 3)  # pretend the wave asks for 200 BPM in the middle
    j2._tables()
    assert j2.thin_spots(), sc
