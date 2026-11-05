"""Wave-fit planner: picks the songs that best follow a drawn tempo wave.

The old picker was greedy: it took the best next song and never looked back. This one scores
whole journeys and searches for the lowest score:

  1. Scale:     map the wave's 0..1 height to BPM, bent toward where your songs actually are.
  2. Shortlist: for each stretch of the wave, keep the songs that fit it best.
  3. Beam:      grow many journeys side by side, keeping the best few hundred at each step.
  4. Repair:    try swapping songs in and out of the best one, keeping changes that help.

Songs sit on the wave by play time (a 6-minute song covers more of it than a 2-minute one),
and the closing album is picked first, so the journey is planned to land on it.

Everything here is plain Python with no GUI or network, so it is easy to test.
"""

from __future__ import annotations

import bisect
import heapq
import math
import random
import statistics
import time
from collections import Counter
from typing import Any

from . import journey as J

LN2 = math.log(2)
FOLD = 0.12  # half/double time counts as this far off (in log-BPM, about 12%)

# How much each rule matters, per priority setting.
PRIORITIES: dict[str, dict[str, float]] = {
    "tight":    {"fit": 2.8, "dir": 1.3, "ef": 0.6, "ew": 0.5, "key": 0.8, "gen": 1.0, "feel": 0.2, "era": 0.1},
    "balanced": {"fit": 1.6, "dir": 0.7, "ef": 1.0, "ew": 0.6, "key": 1.6, "gen": 1.5, "feel": 0.3, "era": 0.2},
    "smooth":   {"fit": 0.7, "dir": 0.35, "ef": 1.7, "ew": 0.4, "key": 1.8, "gen": 1.7, "feel": 0.6, "era": 0.35},
}
PRIORITY_LABELS = {"tight": "Tight to the wave", "balanced": "Balanced", "smooth": "Smooth flow"}
ARTIST_GAP = {1: 4.0, 2: 3.0, 3: 2.0, 4: 1.5}  # same artist k songs back
BUCKETS = 40


def _huber(r: float) -> float:
    r = abs(r)
    return r * r if r <= 1 else 2 * r - 1


# ---------------------------------------------------------------- songs


class Song:
    __slots__ = ("i", "id", "bpm", "lb", "e", "cam", "ci", "genres", "fam", "artist", "album", "dur",
                 "est_bpm", "est_genre", "feel", "year", "shaky")

    def __init__(self, i, t, facts, library):
        f = t.get("features") or {}
        self.i = i
        self.id = t["id"]
        self.bpm = float(f["tempo"])
        self.lb = math.log(self.bpm)
        self.e = f.get("energy")
        self.cam = J.camelot(t)
        self.genres, self.fam = facts
        self.artist = t["artists"][0]["name"] if t.get("artists") else ""
        self.album = (t.get("album") or {}).get("id")
        self.dur = max((t.get("duration_ms") or 210_000) / 60_000, 0.5)  # minutes
        self.est_bpm = bool(f.get("estimated"))
        self.ci = _cam_index(self.cam)
        # "feel": mood, danceability, acousticness and loudness, each 0..1, when ReccoBeats has them
        feel = (f.get("valence"), f.get("danceability"), f.get("acousticness"),
                None if f.get("loudness") is None else min(max((f["loudness"] + 20) / 18, 0.0), 1.0))
        self.feel = feel if all(v is not None for v in feel) else None
        y = ((t.get("album") or {}).get("release_date") or "")[:4]
        self.year = int(y) if y.isdigit() else None
        # tempo detectors often report half or double time; a very fast, very calm song (or a very
        # slow, very intense one) is probably one of those, so its tempo is treated as uncertain
        self.shaky = self.e is not None and ((self.bpm >= 150 and self.e < 0.35) or (self.bpm < 80 and self.e > 0.75))
        self.est_genre = any((library["artists"].get(a.get("id") or a.get("name")) or {}).get("genre_source")
                             == "estimated" for a in (t.get("artists") or [])[:1])


def _cam_index(cam) -> int:
    return 24 if not cam else (cam[0] - 1) * 2 + (cam[1] == "B")


def _key_dist(a, b) -> float:
    if not a or not b:
        return 0.25
    steps = min((a[0] - b[0]) % 12, (b[0] - a[0]) % 12)
    if a[1] == b[1]:
        return {0: 0.0, 1: 0.1}.get(steps, min(1.0, 0.3 + 0.15 * steps))
    return 0.15 if steps == 0 else min(1.0, 0.45 + 0.15 * steps)


_CAMS = [(n, l) for n in range(1, 13) for l in "AB"] + [None]
KEY_TABLE = [[_key_dist(a, b) for b in _CAMS] for a in _CAMS]


def _genre_dist(a: Song, b: Song) -> float:
    """0 for the same genre tags, rising as fewer tags are shared; 1.2 across genre families."""
    if not a.genres or not b.genres:
        return 0.6
    shared = len(a.genres & b.genres)
    if shared:
        return 0.3 * (1 - shared / len(a.genres | b.genres))
    return 0.45 if a.fam and a.fam == b.fam else 1.2


# ---------------------------------------------------------------- the scale


class Scale:
    """Maps wave height (0 = slowest, 1 = fastest) to BPM and back.

    Half linear between the 10th and 90th percentile, half following your songs' own spread,
    so busy tempos get more room on the wave and empty ones less."""

    BEND = 0.5

    def __init__(self, bpms: list[float]):
        bpms = sorted(bpms)
        if len(bpms) >= 10:
            q = statistics.quantiles(bpms, n=10)
            self.low, self.high = q[0], q[-1]
        else:
            self.low, self.high = bpms[0], bpms[-1]
        if self.high - self.low < 2:
            self.low, self.high = self.low - 5, self.high + 5
        n = len(bpms)

        def quant(p):  # empirical quantile
            k = p * (n - 1)
            j = int(k)
            return bpms[j] if j + 1 >= n else bpms[j] + (bpms[j + 1] - bpms[j]) * (k - j)

        self.ys = [k / 100 for k in range(101)]
        bend = self.BEND if n >= 30 else 0.0
        vals = []
        for y in self.ys:
            lin = self.low + (self.high - self.low) * y
            vals.append((1 - bend) * lin + bend * quant(0.1 + 0.8 * y) if n >= 10 else lin)
        for k in range(1, len(vals)):  # keep it strictly rising
            vals[k] = max(vals[k], vals[k - 1] + 1e-3)
        vals[0], vals[-1] = self.low, max(self.high, vals[-2] + 1e-3)
        self.bpms = vals

    def to_bpm(self, y: float) -> float:
        if y <= 0:
            return self.low + (self.high - self.low) * y
        if y >= 1:
            return self.high + (self.high - self.low) * (y - 1)
        k = min(int(y * 100), 99)
        u = y * 100 - k
        return self.bpms[k] + (self.bpms[k + 1] - self.bpms[k]) * u

    def to_y(self, bpm: float) -> float:
        span = self.high - self.low
        if bpm <= self.low:
            return (bpm - self.low) / span
        if bpm >= self.high:
            return 1 + (bpm - self.high) / span
        k = bisect.bisect_right(self.bpms, bpm) - 1
        k = min(max(k, 0), 99)
        a, b = self.bpms[k], self.bpms[k + 1]
        return (k + (bpm - a) / (b - a)) / 100


# ---------------------------------------------------------------- the planner


class Planner:
    def __init__(self, library, pool_tracks, points, length, priority="balanced", start_id=None,
                 handoff=None, seed=None, time_budget=1.2):
        self.library = library
        self.points = J.clean_points(points)
        self.w = PRIORITIES.get(priority, PRIORITIES["balanced"])
        self.rng = random.Random(seed)
        self.seeded = seed is not None
        self.budget = time_budget
        self.songs = [Song(i, t, J._facts(library, t), library) for i, t in enumerate(pool_tracks)]
        self.n = max(2, min(length, len(self.songs))) if self.songs else 0
        self.scale = Scale([s.bpm for s in self.songs]) if self.songs else None
        known_e = sorted(s.e for s in self.songs if s.e is not None)
        if len(known_e) >= 10:
            q = statistics.quantiles(known_e, n=10)
            self.e_low, self.e_high = q[0], q[-1]
        else:
            self.e_low, self.e_high = (known_e[0], known_e[-1]) if known_e else (0.3, 0.8)
        self.start = next((s for s in self.songs if s.id == start_id), None)
        self.handoff = handoff  # a Song-like stand-in for the album's opening track, or None
        self._gd: dict[tuple[int, int], float] = {}
        self._fitc: dict[int, tuple] = {}
        self._pairc: dict[int, float] = {}
        if self.songs:
            self._tables()
        durs = sorted(s.dur for s in self.songs) or [3.5]
        self.med_dur = durs[len(durs) // 2]

    # -- wave helpers

    def _wave_y(self, x):
        return J.curve_at(self.points, min(max(x, 0.0), 1.0))

    RES = 400

    def _tables(self):
        self._fitc = {}
        xs = [k / self.RES for k in range(self.RES + 1)]
        self._ys = [self._wave_y(x) for x in xs]
        self._T = [math.log(self.scale.to_bpm(y)) for y in self._ys]
        self._E = [self.e_low + (self.e_high - self.e_low) * min(max(y, 0.0), 1.0) for y in self._ys]

    def _interp(self, table, x):
        k = x * self.RES
        if k <= 0:
            return table[0]
        if k >= self.RES:
            return table[-1]
        j = int(k)
        return table[j] + (table[j + 1] - table[j]) * (k - j)

    def wave_y(self, x):
        return self._interp(self._ys, x)

    def target_lb(self, x):
        return self._interp(self._T, x)

    def e_target(self, x):
        return self._interp(self._E, x)

    def gdist(self, a, b):
        k = (a.i, b.i) if a.i < b.i else (b.i, a.i)
        d = self._gd.get(k)
        if d is None:
            d = self._gd[k] = _genre_dist(a, b)
        return d

    # -- costs

    def fit(self, s, x):
        """(cost, effective log-BPM, distance, folded) of song s at wave position x."""
        xi = min(max(int(x * self.RES + 0.5), 0), self.RES)
        key = s.i * (self.RES + 1) + xi
        hit = self._fitc.get(key)
        if hit is not None:
            return hit
        t = self._T[xi]
        d = abs(s.lb - t)
        eff, folded = s.lb, False
        fold = FOLD * 0.25 if s.shaky else FOLD  # a probable half/double-time reading folds cheaply
        for alt in (s.lb + LN2, s.lb - LN2):
            if abs(alt - t) + fold < d:
                d, eff, folded = abs(alt - t) + fold, alt, True
        cost = self.w["fit"] * _huber(d / 0.05)
        if s.e is not None:
            cost += self.w["ew"] * _huber((s.e - self._E[xi]) / 0.18)
        else:
            cost += 1.5  # unknown energy shouldn't look like a perfect match
        if s.est_bpm:
            cost += 0.5
        if s.est_genre:
            cost += 0.2
        if s.shaky:
            cost += 0.3
        hit = self._fitc[key] = (cost, eff, d, folded)
        return hit

    def pair(self, prev, cur):
        """The part of a hand-off that depends only on the two songs (cached)."""
        k = prev.i * 1_000_003 + cur.i
        c = self._pairc.get(k)
        if c is not None:
            return c
        w = self.w
        c = 0.0
        if cur.e is not None and prev.e is not None:
            j = abs(cur.e - prev.e)
            c += w["ef"] * (max(0.0, j - 0.08) / 0.1) ** 2
        elif cur.e is None and prev.e is None:
            c += 1.5
        c += w["key"] * 1.5 * KEY_TABLE[prev.ci][cur.ci]
        c += w["gen"] * 1.5 * _genre_dist(prev, cur)
        if prev.feel is not None and cur.feel is not None:
            a, b = prev.feel, cur.feel
            d = 0.35 * abs(a[0] - b[0]) + 0.25 * abs(a[1] - b[1]) + 0.25 * abs(a[2] - b[2]) + 0.15 * abs(a[3] - b[3])
            c += w["feel"] * (max(0.0, d - 0.1) / 0.12) ** 2
        else:
            c += w["feel"] * 0.3  # unknown feel: assume an average hand-off, not a perfect one
        if prev.year and cur.year:
            gap = abs(prev.year - cur.year)
            if gap > 6:
                c += w["era"] * min(2.0, (gap - 6) / 12)
        self._pairc[k] = c
        return c

    def step(self, prev2, prev, cur, prev_eff, cur_eff, x_prev, x_cur, back):
        """Cost of moving from prev to cur. back = the songs before cur, newest last."""
        w = self.w
        res = self.RES
        dw = self._T[min(max(int(x_cur * res + 0.5), 0), res)] - self._T[min(max(int(x_prev * res + 0.5), 0), res)]
        r = abs(((cur_eff - prev_eff) - dw) / 0.06)
        cost = w["dir"] * (r * r if r <= 1 else 2 * r - 1) + self.pair(prev, cur)
        if prev2 is not None and cur.fam and prev.fam and cur.fam == prev2.fam != prev.fam:
            cost += w["gen"]  # genre ping-pong: A, B, A
        n = len(back)
        for k in range(1, 5 if n >= 4 else n + 1):
            b = back[-k]
            if b.artist and b.artist == cur.artist:
                cost += ARTIST_GAP[k]
            if k <= 3 and b.album and b.album == cur.album:
                cost += 1.0
        return cost

    def handoff_cost(self, last, last_eff):
        h = self.handoff
        if h is None:
            return 0.0
        w = self.w
        cost = w["dir"] * _huber((h.lb - last_eff) / 0.08) * 0.5
        if h.e is not None and last.e is not None:
            cost += w["ef"] * (max(0.0, abs(h.e - last.e) - 0.08) / 0.1) ** 2
        cost += w["key"] * 1.5 * _key_dist(last.cam, h.cam)
        cost += w["gen"] * 1.5 * _genre_dist(last, h)
        return cost

    # -- whole-journey score

    def positions(self, chain):
        mids, t = [], 0.0
        for s in chain:
            mids.append(t + s.dur / 2)
            t += s.dur
        if len(chain) < 2:
            return [0.0] * len(chain)
        span = mids[-1] - mids[0] or 1.0
        return [(m - mids[0]) / span for m in mids]

    def score(self, chain, detail=False):
        xs = self.positions(chain)
        total = 0.0
        effs, parts = [], []
        for i, s in enumerate(chain):
            fc, eff, d, folded = self.fit(s, xs[i])
            effs.append(eff)
            sc = 0.0
            if i:
                sc = self.step(chain[i - 2] if i >= 2 else None, chain[i - 1], s, effs[i - 1], eff,
                               xs[i - 1], xs[i], chain[max(0, i - 4):i])
            total += fc + sc
            if detail:
                parts.append({"x": xs[i], "target": math.exp(self.target_lb(xs[i])), "off": d,
                              "folded": folded, "fit_cost": fc, "step_cost": sc})
        if chain:
            total += self.handoff_cost(chain[-1], effs[-1])
        return (total, xs, effs, parts) if detail else total

    def _local(self, chain, xs, effs, i):
        """Cost of the terms touched by changing position i (xs held fixed)."""
        e = list(effs)
        fc0, e[i], _, _ = self.fit(chain[i], xs[i])
        total = fc0
        for j in range(max(i, 1), min(len(chain), i + 5)):
            total += self.step(chain[j - 2] if j >= 2 else None, chain[j - 1], chain[j], e[j - 1], e[j],
                               xs[j - 1], xs[j], chain[max(0, j - 4):j])
        if i == len(chain) - 1:
            total += self.handoff_cost(chain[-1], e[-1])
        return total

    # -- search

    def shortlists(self, k):
        """Per bucket of the wave, the k songs that fit it best on their own."""
        lists = []
        for b in range(BUCKETS + 1):
            x = b / BUCKETS
            banned = getattr(self, "_banned", ())
            scored = sorted((s for s in self.songs if s.i not in banned), key=lambda s: self.fit(s, x)[0])
            lists.append(scored[:k])
        return lists

    def plan(self) -> list[Song]:
        if not self.songs:
            return []
        t0 = time.perf_counter()
        n = self.n
        pool = len(self.songs)
        k = min(pool, getattr(self, "k_override", None) or 90)
        beam_w = getattr(self, "beam_override", None) or (80 if n <= 30 else 60)
        if self.seeded:  # "Try another": leave out a random sixth of the songs so it really differs
            keep = {s.i for s in self.songs if self.rng.random() > 0.16 or s is self.start}
            self._banned = {s.i for s in self.songs} - keep
        else:
            self._banned = set()
        self.short = short = self.shortlists(k)
        span_est = (n - 1) * self.med_dur
        noise = 0.6 if self.seeded else 0.0

        # a state: (cost, tiebreak, chain tuple, effs tuple, used bitmask, elapsed end, first mid)
        if self.start:
            fc, eff, _, _ = self.fit(self.start, 0.0)
            beam = [(fc, 0, (self.start,), (eff,), 1 << self.start.i, self.start.dur, self.start.dur / 2)]
        else:
            beam = []
            for s in short[0]:
                fc, eff, _, _ = self.fit(s, 0.0)
                beam.append((fc + noise * self.rng.random(), 0, (s,), (eff,), 1 << s.i, s.dur, s.dur / 2))
            beam = heapq.nsmallest(beam_w, beam, key=lambda st: st[0])

        tick = 0
        for pos in range(1, n):
            last_step = pos == n - 1
            kids = []
            for cost, _, chain, effs, used, end, mid0 in beam:
                prev = chain[-1]
                prev2 = chain[-2] if len(chain) > 1 else None
                x_prev = ((end - prev.dur / 2) - mid0) / span_est
                x_guess = (end + self.med_dur / 2 - mid0) / span_est
                cands = short[min(max(round(x_guess * BUCKETS), 0), BUCKETS)]
                back = chain[-4:]
                for c in cands:
                    if used >> c.i & 1:
                        continue
                    x = 1.0 if last_step else min((end + c.dur / 2 - mid0) / span_est, 1.0)
                    fc, eff, _, _ = self.fit(c, x)
                    sc = self.step(prev2, prev, c, effs[-1], eff, min(x_prev, 1.0), x, back)
                    total = cost + fc + sc
                    if last_step:
                        total += self.handoff_cost(c, eff)
                    if noise:
                        total += noise * self.rng.random()
                    tick += 1
                    kids.append((total, tick, chain, effs, used, end, mid0, c, eff))
            if not kids:
                break
            kids.sort(key=lambda kd: kd[0])
            beam, per_last = [], Counter()
            for total, tb, chain, effs, used, end, mid0, c, eff in kids:
                if per_last[c.i] >= 3:  # keep the beam varied
                    continue
                per_last[c.i] += 1
                beam.append((total, tb, chain + (c,), effs + (eff,), used | (1 << c.i), end + c.dur, mid0))
                if len(beam) >= beam_w:
                    break

        finals = sorted(beam, key=lambda st: self.score(list(st[2])))
        if self.seeded and len(finals) > 1:
            best_score = self.score(list(finals[0][2]))
            close = [st for st in finals[:6] if self.score(list(st[2])) <= best_score * 1.04 + 0.5]
            starts = [list(self.rng.choice(close)[2])]
        else:
            # repair a few different finishers: the best one before repair isn't always best after
            starts, seen = [], set()
            for st in finals:
                key = frozenset(s.i for s in st[2])
                if key not in seen:
                    seen.add(key)
                    starts.append(list(st[2]))
                if len(starts) == 3:
                    break
        best_chain, best_score = None, math.inf
        for k, chain in enumerate(starts):
            left = self.budget - (time.perf_counter() - t0)
            share = max(0.1, left / (len(starts) - k))
            chain = self.repair(chain, share)
            sc = self.score(chain)
            if sc < best_score:
                best_chain, best_score = chain, sc
        return best_chain

    def _near(self, x, spread=0.07):
        """All songs whose tempo (or half/double tempo) is within ~7% of the wave at x."""
        if not hasattr(self, "_by_lb"):
            self._by_lb = sorted(self.songs, key=lambda s: s.lb)
            self._lbs = [s.lb for s in self._by_lb]
        t = self.target_lb(x)
        out = []
        for c in (t, t - LN2, t + LN2):
            lo = bisect.bisect_left(self._lbs, c - spread)
            hi = bisect.bisect_right(self._lbs, c + spread)
            out += self._by_lb[lo:hi]
        return out

    def repair(self, chain, budget):
        """Local search: swap a song for an unused one, swap neighbors, or move a song a few
        places, keeping any change that lowers the total score."""
        t_end = time.perf_counter() + budget
        best = self.score(chain)
        fixed = 1 if self.start else 0
        n = len(chain)
        improved = True
        while improved and time.perf_counter() < t_end:
            improved = False
            total, xs, effs, _ = self.score(chain, detail=True)
            used = {s.i for s in chain}
            order = list(range(fixed, n))
            if self.seeded:
                self.rng.shuffle(order)
            for i in order:
                if time.perf_counter() > t_end:
                    break
                # 1. replace with any unused song near the wave here (not just the shortlist)
                base = self._local(chain, xs, effs, i)
                trial = []
                old = chain[i]
                for c in self._near(xs[i]):
                    if c.i in used or c.i in self._banned:
                        continue
                    chain[i] = c
                    v = self._local(chain, xs, effs, i)
                    if v < base - 1e-9:
                        trial.append((v, c))
                chain[i] = old
                trial.sort(key=lambda t: t[0])
                for _, c in trial[:3]:  # confirm with the exact score (play times shift)
                    chain[i] = c
                    new = self.score(chain)
                    if new < best - 1e-9:
                        best, improved = new, True
                        used.discard(old.i)
                        used.add(c.i)
                        total, xs, effs, _ = self.score(chain, detail=True)
                        break
                    chain[i] = old
                # 2. move this song up to 3 places later or earlier (covers neighbor swaps)
                for j in range(max(fixed, i - 3), min(n, i + 4)):
                    if j == i:
                        continue
                    trial_chain = chain[:i] + chain[i + 1:]
                    trial_chain.insert(j, chain[i])
                    new = self.score(trial_chain)
                    if new < best - 1e-9:
                        chain[:] = trial_chain
                        best, improved = new, True
                        total, xs, effs, _ = self.score(chain, detail=True)
                        break
        return chain

    # -- reporting

    def thin_spots(self, samples=60, need=3):
        """Stretches of the wave where fewer than `need` songs sit within ~6% of the target."""
        lbs = sorted(s.lb for s in self.songs)
        spans, start = [], None
        for k in range(samples + 1):
            x = k / samples
            t = self.target_lb(x)
            count = bisect.bisect_right(lbs, t + 0.06) - bisect.bisect_left(lbs, t - 0.06)
            thin = count < need
            if thin and start is None:
                start = x
            if not thin and start is not None:
                spans.append((start, x))
                start = None
        if start is not None:
            spans.append((start, 1.0))
        merged = []
        for a, b in spans:  # join stretches separated by a small gap, so the graph isn't striped
            if merged and a - merged[-1][1] < 0.08:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        return merged

    def report(self, chain):
        total, xs, effs, parts = self.score(chain, detail=True)
        details = []
        for i, s in enumerate(chain):
            p = parts[i]
            prev = chain[i - 1] if i else None
            info = {
                "x": p["x"], "target_bpm": p["target"], "off_pct": (math.exp(p["off"]) - 1) * 100,
                "folded": p["folded"], "cost": p["fit_cost"] + p["step_cost"],
                "energy_step": (abs(s.e - prev.e) if prev and s.e is not None and prev.e is not None
                                else None),
                "key_step": _key_dist(prev.cam, s.cam) if prev else None,
                "genre_step": self.gdist(prev, s) if prev else None,
                "feel_step": (sum(abs(a - b) for a, b in zip(prev.feel[:3], s.feel[:3])) / 3
                              if prev and prev.feel and s.feel else None),
                "year_gap": abs(prev.year - s.year) if prev and prev.year and s.year else None,
                "tempo_guess": s.shaky,
                "artist_repeat": any(b.artist == s.artist for b in chain[max(0, i - 4):i]) if s.artist else False,
            }
            details.append(info)
        # 0-100 score: 60% how close to the wave, 40% how smooth the hand-offs are
        wave = statistics.mean(max(0.0, 1 - parts[i]["off"] / 0.15) for i in range(len(chain)))
        flows = []
        for d in details[1:]:
            ej = d["energy_step"] if d["energy_step"] is not None else 0.2
            flows.append(0.5 * (1 - min(1.0, ej / 0.4)) + 0.25 * (1 - d["key_step"])
                         + 0.25 * (1 - min(1.0, d["genre_step"] / 1.2)))
        flow = statistics.mean(flows) if flows else 1.0
        return {
            "score_raw": total,
            "fit_score": round(100 * (0.6 * wave + 0.4 * flow)),
            "xs": xs,
            "targets": [d["target_bpm"] for d in details],
            "details": details,
            "thin": self.thin_spots(),
        }


def scale_for(library, entries, genre_family=None) -> Scale | None:
    """The BPM scale the planner would use for these songs (for drawing the wave before a build)."""
    tracks = library["tracks"]
    bpms = [J.tempo(tracks[e["id"]]) for e in entries
            if e["id"] in tracks and J.tempo(tracks[e["id"]])
            and (not genre_family or J._family(library, tracks[e["id"]]) == genre_family)]
    return Scale(bpms) if bpms else None


def why(detail: dict[str, Any], prev_name: str | None) -> str:
    """A short plain-English reason for a song's place, for a tooltip."""
    lines = [f"Wave wants ~{detail['target_bpm']:.0f} BPM here; this song is "
             f"{detail['off_pct']:.0f}% off" + (" (counted at half/double time)" if detail["folded"] else "")]
    if prev_name:
        if detail["energy_step"] is not None:
            lines.append(f"Energy change from “{prev_name}”: {detail['energy_step'] * 100:.0f} points")
        else:
            lines.append("Energy unknown, so it pays a small penalty")
        k = detail["key_step"]
        lines.append("Key: " + ("perfect match" if k == 0 else "smooth (next door on the Camelot wheel)" if k <= 0.15
                                else "unknown" if k == 0.25 else "a bit of a jump" if k < 0.6 else "a clash"))
        g = detail["genre_step"]
        lines.append("Genre: " + ("same as the last song" if g == 0 else "same family" if g <= 0.35
                                  else "unknown" if g == 0.6 else "a change of genre"))
        if detail.get("feel_step") is not None:
            f = detail["feel_step"]
            lines.append("Mood and sound: " + ("very close" if f < 0.08 else "close" if f < 0.15 else "a noticeable change"))
        if detail.get("year_gap") is not None and detail["year_gap"] > 8:
            lines.append(f"Era: {detail['year_gap']} years apart")
    if detail.get("tempo_guess"):
        lines.append("Its BPM looks like a half/double-time reading, so Loom treats it loosely")
    if detail["artist_repeat"]:
        lines.append("Same artist as a song just before it (nothing better fit)")
    return "\n".join(lines)


# ---------------------------------------------------------------- album first


def _album_groups(library, entries):
    tracks = library["tracks"]
    by_album: dict[str, dict[str, dict]] = {}
    for e in entries:
        t = tracks.get(e["id"])
        if t and (t.get("album") or {}).get("id"):
            by_album.setdefault(t["album"]["id"], {})[t["id"]] = t
    return {a: list(s.values()) for a, s in by_album.items()}


def choose_album(library, entries, planner: Planner, genre_family=None, exclude=()):
    """Pick the closing album before the journey, so the journey can be planned to land on it.

    Returns (album info, opener stand-in) or (None, None)."""
    if not planner.songs:
        return None, None
    end_lb = planner.target_lb(1.0)
    e_end = planner.e_target(1.0)
    tail = sorted(planner.songs, key=lambda s: planner.fit(s, 1.0)[0])[:12]
    best = None
    for album_id, songs in _album_groups(library, entries).items():
        known = [t for t in songs if J.tempo(t)]
        if not known:
            continue
        first = songs[0]
        name = first["album"]["name"]
        if not name or (len(songs) == 1 and name.lower() == first["name"].lower()):
            continue  # a single, not an album
        if genre_family and not any(J._family(library, t) == genre_family for t in songs):
            continue
        ss = [Song(-1 - k, t, J._facts(library, t), library) for k, t in enumerate(known)]
        mean_lb = statistics.mean(s.lb for s in ss)
        cost = abs(mean_lb - end_lb) / 0.035
        es = [s.e for s in ss if s.e is not None]
        if es:
            cost += abs(statistics.mean(es) - e_end) / 0.15
        cost += min(_genre_dist(a, b) for a in tail for b in ss) * 1.5
        cost -= min(len(songs), 5) * 0.25  # prefer albums you clearly like
        opener = None
        numbered = [(t.get("disc_number") or 1, t.get("track_number"), k) for k, t in enumerate(known)
                    if t.get("track_number")]
        if numbered:
            d, tn, k = min(numbered)
            if d == 1 and tn == 1:
                opener = ss[k]
                cost += abs(opener.lb - end_lb) / 0.06
        if best is None or cost < best[0]:
            best = (cost, album_id, songs, ss, opener)
    if not best:
        return None, None
    _, album_id, songs, ss, opener = best
    if opener is None:  # a stand-in with the album's average feel
        opener = Song.__new__(Song)
        opener.i, opener.id = -999, None
        opener.lb = statistics.mean(s.lb for s in ss)
        opener.bpm = math.exp(opener.lb)
        es = [s.e for s in ss if s.e is not None]
        opener.e = statistics.mean(es) if es else None
        opener.cam, opener.ci, opener.feel, opener.year, opener.shaky = None, 24, None, None, False
        opener.genres = frozenset().union(*(s.genres for s in ss))
        opener.fam = ss[0].fam
        opener.artist, opener.album, opener.dur = ss[0].artist, album_id, 4.0
        opener.est_bpm = opener.est_genre = False
    first = songs[0]
    info = {
        "id": album_id,
        "name": first["album"]["name"],
        "artist": first["artists"][0]["name"] if first["artists"] else "",
        "known_track_ids": [t["id"] for t in songs],
        "avg_bpm": statistics.mean(J.tempo(t) for t in songs if J.tempo(t)),
    }
    return info, opener


# ---------------------------------------------------------------- entry point


def build(library, entries, shape="wind_down", length=25, start_id=None, genre_family=None,
          end_with_album=True, seed=None, priority="balanced", time_budget=1.2,
          thorough=False) -> dict[str, Any]:
    """thorough: search wider and longer (about 1.5 s); used for Build and Try another, while
    live edits of the wave use the quicker search."""
    J._memo.clear()
    tracks = library["tracks"]
    points = J.shape_points(shape)
    pool, seen = [], set()
    for e in entries:
        t = tracks.get(e["id"])
        if not t or t["id"] in seen or not J.tempo(t):
            continue
        if genre_family and J._family(library, t) != genre_family and t["id"] != start_id:
            continue
        seen.add(t["id"])
        pool.append(t)
    skipped = sum(1 for e in entries if e["id"] in tracks and not J.tempo(tracks[e["id"]]))
    empty = {"track_ids": [], "targets": [], "start_bpm": None, "low": None, "high": None,
             "skipped": skipped, "album": None, "fit_score": None, "details": [], "xs": [],
             "thin": [], "scale": None, "short_pool": False}
    if not pool:
        return empty

    album, opener = None, None
    if end_with_album:
        probe = Planner(library, pool, points, length, priority, start_id, seed=seed)
        album, opener = choose_album(library, entries, probe, genre_family)
        if album:
            drop = set(album["known_track_ids"]) - {start_id}
            rest = [t for t in pool if t["id"] not in drop]
            if len(rest) >= 2:
                pool = rest
            else:
                album, opener = None, None

    planner = Planner(library, pool, points, length, priority, start_id, handoff=opener, seed=seed,
                      time_budget=time_budget)
    if thorough:
        planner.beam_override = 160
        planner.budget = max(time_budget, 2.5)
    chain = planner.plan()
    rep = planner.report(chain)
    return {
        "track_ids": [s.id for s in chain],
        "targets": rep["targets"],
        "xs": rep["xs"],
        "details": rep["details"],
        "fit_score": rep["fit_score"],
        "score_raw": rep["score_raw"],
        "thin": rep["thin"],
        "scale": planner.scale,
        "start_bpm": chain[0].bpm if chain else None,
        "low": planner.scale.low,
        "high": planner.scale.high,
        "skipped": skipped,
        "album": album,
        "short_pool": len(pool) < length,
        "priority": priority,
    }
