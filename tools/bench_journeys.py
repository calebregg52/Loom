"""Test bench: the wave-fit planner vs the old greedy picker on libraries shaped like a real one.

Run:  python tools/bench_journeys.py [--waves 50] [--size 2300]
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom import journey, wavefit  # noqa: E402

GENRES = {  # family: (genre tags, BPM mean, BPM spread, share of library)
    "country": (["country", "red dirt"], 112, 22, 0.45),
    "rock": (["classic rock", "rock"], 124, 24, 0.30),
    "hip hop": (["hip hop", "trap"], 92, 14, 0.25),
}


def make_library(size=2300, seed=0, octave_errors=0.05):
    """A synthetic library shaped like a real one: genre-shaped tempo, energy, mood and era,
    a share of songs with no BPM, some with BPM only (Deezer), and some BPMs reported at
    half or double time (a common tempo-detection mistake)."""
    rng = random.Random(seed)
    artists, tracks, liked = {}, {}, []
    era = {}
    for fam, (tags, *_rest) in GENRES.items():
        for a in range(int(400 * GENRES[fam][3])):
            name = f"{fam}-{a}"
            artists[name] = {"name": name, "genres": tags[: rng.randint(1, 2)], "genre_source": "lastfm"}
            era[name] = rng.randint(1965, 2022)
    names = list(artists)
    weights = [GENRES[n.rsplit("-", 1)[0]][3] / (400 * GENRES[n.rsplit("-", 1)[0]][3]) for n in names]
    for i in range(size):
        a = rng.choices(names, weights)[0]
        fam = a.rsplit("-", 1)[0]
        _, mu, sd, _ = GENRES[fam]
        bpm = max(60, min(190, rng.gauss(mu, sd)))
        e = max(0.02, min(0.99, 0.2 + (bpm - 60) / 130 * 0.55 + rng.gauss(0, 0.16)))
        ac = max(0.0, min(1.0, (0.55 if fam == "country" else 0.15) - (e - 0.5) * 0.6 + rng.gauss(0, 0.15)))
        dance = max(0.0, min(1.0, (0.75 if fam == "hip hop" else 0.5) + rng.gauss(0, 0.12)))
        val = max(0.0, min(1.0, 0.3 + e * 0.4 + rng.gauss(0, 0.2)))
        loud = -14 + e * 10 + rng.gauss(0, 1.5)
        reported = bpm
        if rng.random() < octave_errors:
            reported = bpm * 2 if bpm < 95 else bpm / 2
        roll = rng.random()
        if roll < 0.27:
            feats = None  # no BPM found
        elif roll < 0.38:
            feats = {"tempo": reported, "source": "deezer"}  # BPM but no energy/key
        elif roll < 0.42:
            feats = {"tempo": bpm, "source": "artist average", "estimated": True}
        else:
            feats = {"tempo": reported, "energy": e, "key": rng.randint(0, 11), "mode": rng.randint(0, 1),
                     "valence": val, "danceability": dance, "acousticness": ac, "loudness": loud,
                     "source": "reccobeats"}
        album = f"{a}-lp{rng.randint(0, 3)}"
        year = era[a] + rng.randint(0, 8)
        tracks[f"t{i}"] = {
            "id": f"t{i}", "name": f"Song {i}", "artists": [{"id": a, "name": a}],
            "album": {"id": album, "name": album, "release_date": str(year)},
            "duration_ms": int(max(100, rng.gauss(215, 50)) * 1000),
            "track_number": rng.randint(1, 12), "disc_number": 1, "features": feats,
            "_true_bpm": bpm,
        }
        liked.append({"id": f"t{i}"})
    return {"user": {"id": "me"}, "tracks": tracks, "liked": liked, "playlists": [], "artists": artists}


def random_wave(rng):
    n = rng.randint(2, 7)
    xs = sorted(rng.random() for _ in range(n - 2))
    return [(0, rng.random())] + [(x, rng.random()) for x in xs] + [(1, rng.random())]


def measure(library, entries, points, result, ref: wavefit.Planner):
    """The same yardsticks for any journey, judged by the planner's scoring and scale."""
    tracks = library["tracks"]
    by_id = {s.id: s for s in ref.songs}
    chain = [by_id[t] for t in result["track_ids"] if t in by_id]
    total, xs, effs, parts = ref.score(chain, detail=True)
    offs = [p["off"] for p in parts]
    ejumps = [abs(a.e - b.e) for a, b in zip(chain, chain[1:]) if a.e is not None and b.e is not None]
    keys = [wavefit._key_dist(a.cam, b.cam) for a, b in zip(chain, chain[1:]) if a.cam and b.cam]
    repeats = sum(1 for i, s in enumerate(chain) if any(b.artist == s.artist for b in chain[max(0, i - 4):i]))
    return {
        "off": statistics.mean(offs) * 100,
        "max_ej": max(ejumps) if ejumps else 0,
        "key_ok": (sum(k <= 0.1 for k in keys) / len(keys) * 100) if keys else 100,
        "repeats": repeats,
        "score": total,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--waves", type=int, default=50)
    ap.add_argument("--size", type=int, default=2300)
    ap.add_argument("--length", type=int, default=25)
    ap.add_argument("--priority", default="balanced")
    ap.add_argument("--bend", type=float, default=None, help="override the scale bend (0 = linear, like greedy)")
    args = ap.parse_args()
    if args.bend is not None:
        wavefit.Scale.BEND = args.bend
    lib = make_library(args.size)
    entries = lib["liked"]
    rng = random.Random(7)
    waves = [(k, journey.shape_points(k)) for k in journey.PRESETS]
    waves += [(f"random{i}", random_wave(rng)) for i in range(args.waves)]
    rows = {"greedy": [], "wavefit": []}
    times = []
    wins = 0
    for name, pts in waves:
        pool = [lib["tracks"][e["id"]] for e in entries if journey.tempo(lib["tracks"][e["id"]])]
        ref = wavefit.Planner(lib, pool, pts, args.length, args.priority)
        g = journey.build_greedy(lib, entries, pts, args.length, end_with_album=False)
        t0 = time.perf_counter()
        w = journey.build(lib, entries, pts, args.length, end_with_album=False, priority=args.priority)
        times.append(time.perf_counter() - t0)
        mg, mw = measure(lib, entries, pts, g, ref), measure(lib, entries, pts, w, ref)
        rows["greedy"].append(mg)
        rows["wavefit"].append(mw)
        wins += mw["score"] < mg["score"]
    print(f"{len(waves)} waves · {args.size} songs · {args.length} long · priority {args.priority}")
    print(f"{'':10}{'off wave %':>12}{'max energy jump':>18}{'key ok %':>10}{'artist repeats':>16}{'score':>9}")
    for k, rs in rows.items():
        print(f"{k:10}{statistics.mean(r['off'] for r in rs):12.1f}"
              f"{statistics.mean(r['max_ej'] for r in rs):18.2f}{statistics.mean(r['key_ok'] for r in rs):10.0f}"
              f"{statistics.mean(r['repeats'] for r in rs):16.2f}{statistics.mean(r['score'] for r in rs):9.1f}")
    gw = statistics.mean(r["off"] for r in rows["greedy"])
    ww = statistics.mean(r["off"] for r in rows["wavefit"])
    print(f"wave distance {100 * (1 - ww / gw):.0f}% lower · better score on {wins}/{len(waves)} waves · "
          f"time avg {statistics.mean(times):.2f}s, max {max(times):.2f}s")
    print(f"worst max energy jump {max(r['max_ej'] for r in rows['wavefit']):.2f}")


if __name__ == "__main__":
    main()
