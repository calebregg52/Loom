"""Compare two checkouts of Loom on the same synthetic library: python tools/compare_versions.py <repo root> [priority].
Set WF_THOROUGH=1 for the Build-button search, WF_W={...} to try weights."""
import os, sys, json, math, random, statistics, time
root = sys.argv[1]
sys.path.insert(0, "/home/claude/loom/tools")
from bench_journeys import make_library, random_wave   # new generator (has mood/year/octave errors)
for k in [k for k in sys.modules if k == "loom" or k.startswith("loom.")]:
    del sys.modules[k]
sys.path = [p for p in sys.path if p.rstrip("/") != "/home/claude/loom"]
sys.path.insert(0, root)
from loom import journey, wavefit
import os
if os.environ.get("WF_W"):
    wavefit.PRIORITIES[sys.argv[2] if len(sys.argv) > 2 else "balanced"].update(json.loads(os.environ["WF_W"]))
assert wavefit.__file__.startswith(root), wavefit.__file__
lib = make_library(2300)
rng = random.Random(7)
waves = [journey.shape_points(k) for k in journey.PRESETS] + [random_wave(rng) for _ in range(35)]
prio = sys.argv[2] if len(sys.argv) > 2 else "balanced"
T = lib["tracks"]
def m(j):
    ids = j["track_ids"]; tr = [T[i] for i in ids]
    out = {}
    # wave distance, using each run's own targets; and against TRUE tempo (octave errors)
    offs, toffs = [], []
    for t, tgt in zip(tr, j["targets"]):
        b = t["features"]["tempo"]; tb = t["_true_bpm"]
        best = min(abs(math.log(b * f / tgt)) for f in (1, 2, .5))
        offs.append(best)
        toffs.append(min(abs(math.log(tb * f / tgt)) for f in (1, 2, .5)))
    out["off"] = statistics.mean(offs) * 100
    out["true_off"] = statistics.mean(toffs) * 100
    f = [t["features"] for t in tr]
    ej = [abs(a["energy"] - b["energy"]) for a, b in zip(f, f[1:]) if a.get("energy") is not None and b.get("energy") is not None]
    out["ej_avg"] = statistics.mean(ej) if ej else 0; out["ej_max"] = max(ej) if ej else 0
    cams = [journey.camelot(t) for t in tr]
    kd = [wavefit._key_dist(a, b) for a, b in zip(cams, cams[1:]) if a and b]
    out["key_ok"] = sum(k <= 0.1 for k in kd) / len(kd) * 100 if kd else 100
    out["key_clash"] = sum(k >= 0.6 for k in kd) / len(kd) * 100 if kd else 0
    def feel(x): return None if x.get("valence") is None else (x["valence"], x["danceability"], x["acousticness"])
    fl = [sum(abs(p - q) for p, q in zip(feel(a), feel(b))) / 3 for a, b in zip(f, f[1:]) if feel(a) and feel(b)]
    out["feel_avg"] = statistics.mean(fl) if fl else 0
    yrs = [int(t["album"]["release_date"]) for t in tr]
    out["year_gap"] = statistics.mean(abs(a - b) for a, b in zip(yrs, yrs[1:]))
    fams = [lib["artists"][t["artists"][0]["id"]]["genres"][0] for t in tr]
    out["genre_switch"] = sum(a != b for a, b in zip(fams, fams[1:]))
    out["unknown_energy"] = sum(1 for x in f if x.get("energy") is None)
    out["octave_err_songs"] = sum(1 for t in tr if abs(math.log(t["_true_bpm"] / t["features"]["tempo"])) > 0.5)
    return out
rows, times = [], []
for pts in waves:
    t0 = time.perf_counter()
    kw = {"thorough": True} if os.environ.get("WF_THOROUGH") else {}
    j = journey.build(lib, lib["liked"], pts, 25, end_with_album=False, priority=prio, **kw)
    times.append(time.perf_counter() - t0)
    rows.append(m(j))
res = {k: statistics.mean(r[k] for r in rows) for k in rows[0]}
res["time"] = statistics.mean(times); res["time_max"] = max(times)
res["worst_ej_max"] = max(r["ej_max"] for r in rows)
print(json.dumps(res))
