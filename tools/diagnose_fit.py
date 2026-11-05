"""Diagnose the wave-fit planner: how close to optimal is the search, which rules cost the
most, and what the worst songs and hand-offs look like. Uses the bench's synthetic library."""

from __future__ import annotations

import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_journeys import make_library, random_wave  # noqa: E402

from loom import journey, wavefit  # noqa: E402


def term_breakdown(pl, chain):
    """Split the total score into its rules, by re-running the cost with one weight at a time."""
    parts = defaultdict(float)
    xs = pl.positions(chain)
    effs = []
    for i, s in enumerate(chain):
        t = pl.target_lb(xs[i])
        fc, eff, d, folded = pl.fit(s, xs[i])
        effs.append(eff)
        parts["wave fit"] += pl.w["fit"] * wavefit._huber(d / 0.05)
        if s.e is not None:
            parts["energy vs wave"] += pl.w["ew"] * wavefit._huber((s.e - pl.e_target(xs[i])) / 0.18)
        else:
            parts["unknown energy"] += 1.0
        parts["estimates"] += 0.5 * s.est_bpm + 0.2 * s.est_genre
        if i:
            p = chain[i - 1]
            dw = pl.target_lb(xs[i]) - pl.target_lb(xs[i - 1])
            parts["direction"] += pl.w["dir"] * wavefit._huber(((eff - effs[i - 1]) - dw) / 0.06)
            if s.e is not None and p.e is not None:
                parts["energy step"] += pl.w["ef"] * (max(0.0, abs(s.e - p.e) - 0.08) / 0.1) ** 2
            parts["key"] += pl.w["key"] * 1.5 * wavefit._key_dist(p.cam, s.cam)
            parts["genre"] += pl.w["gen"] * 1.5 * pl.gdist(p, s)
    total = pl.score(chain)
    parts["other (variety, ping-pong, extras)"] = total - sum(parts.values())
    return parts, total


def main():
    lib = make_library(2300)
    rng = random.Random(11)
    waves = [(k, journey.shape_points(k)) for k in journey.PRESETS] + \
            [(f"random{i}", random_wave(rng)) for i in range(15)]
    pool = [t for t in lib["tracks"].values() if journey.tempo(t)]
    gaps, totals = [], defaultdict(float)
    worst = []
    t_norm, t_big = [], []
    for name, pts in waves:
        pl = wavefit.Planner(lib, pool, pts, 25)
        t0 = time.perf_counter(); chain = pl.plan(); t_norm.append(time.perf_counter() - t0)
        big = wavefit.Planner(lib, pool, pts, 25, time_budget=6)
        big.beam_override, big.k_override = 700, 140
        t0 = time.perf_counter(); bchain = big.plan(); t_big.append(time.perf_counter() - t0)
        s1, s2 = pl.score(chain), pl.score(bchain)
        gaps.append((s1 - s2) / max(s2, 1e-9) * 100)
        parts, total = term_breakdown(pl, chain)
        for k, v in parts.items():
            totals[k] += v / len(waves)
        _, xs, effs, det = pl.score(chain, detail=True)
        for i, (s, d) in enumerate(zip(chain, det)):
            worst.append((d["off"], name, i, s.bpm, math.exp(pl.target_lb(xs[i])), d["folded"]))
        # true-tempo check: songs whose reported BPM was an octave error
        octave = [s for s in chain if abs(math.log(lib["tracks"][s.id]["_true_bpm"] / s.bpm)) > 0.5]
        if octave:
            totals["songs with wrong (octave) BPM in journey"] += len(octave) / len(waves)
    print(f"Search gap vs a 4x larger search: mean {statistics.mean(gaps):+.1f}%  max {max(gaps):+.1f}%  "
          f"(time {statistics.mean(t_norm):.2f}s vs {statistics.mean(t_big):.2f}s)")
    print("Average score by rule (25-song journeys):")
    for k, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {k:42}{v:7.2f}")
    worst.sort(reverse=True)
    print("Worst wave misses:")
    for off, name, i, bpm, tgt, folded in worst[:8]:
        print(f"  {name:14} #{i + 1:<3} {bpm:6.1f} BPM vs {tgt:6.1f} wanted  off {off * 100:4.1f}%  folded={folded}")
    print(f"Songs >5% off: {sum(w[0] > 0.049 for w in worst)}/{len(worst)}")


if __name__ == "__main__":
    main()
