#!/usr/bin/env python3
"""Leave-one-run-out selection of a causal FIX-label rule (WP44).

    python fix_rule_loro.py --data-root .../PPC-Dataset ne_s10 ne_s11 ne_s12

Runs must carry the WP44 diagnostics (`rb_nb_at`, `rb_nb_at_ne`,
`rb_ne_mass`, `base_smode`, `base_nb`). Positions are never changed; a rule
only decides which epochs are labelled FIX:

    FIX  <=>  nb_at[t] >= F                       PF: MAP pairs above agreement t
          or  (nb_at_ne[t2] >= F2 and ne_mass >= M)
                                                  PF: integer hypotheses agree
                                                  while the flat one holds mass
          or  base_smode == 4 and base_nb >= B     shared filter's own LAMBDA
                                                  fix (phase 1 fix-and-hold,
                                                  phase 2 ratio test)

Every label also passes the PF's own FIX-output guards (reported position
within 5 m of the code-only DDPR position, and the float / DDPR / fix vote).

For each held-out run, the grid point that maximises correct FIX on the
other two runs (all seeds pooled) while keeping every training run's wrong
FIX share <= --cap (default 0.7%, a margin under the 1% target) is applied
to the held-out run. With --fixed t,F,t2,F2,M,B the given rule is scored on
every run instead (no selection).
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from fix_rules import errors
from rbpf_fgo import NB_AT_THRESH

GRID = list(itertools.product(
    (3, 4, 5),            # t index  (0.95, 0.98, 0.99)
    (5, 9, 12),           # F
    (3, 5),               # t2 index (0.95, 0.99)
    (12, 15, 99),         # F2 (99 = off)
    (0.1, 0.3),           # M
    (5, 9, 99),           # B (99 = off)
))


GUARD_M = float(__import__("os").environ.get("FIX_RULE_GUARD_M", "5.0"))


def prepare(d, run, data_root):
    e, n = errors(d, run, data_root)
    ok = np.isfinite(e) & (np.asarray(d["smode"]) != 6)
    # the PF's own FIX-output guards (RBPF_FIX_MAX_DPR = 5 m and the
    # float / DDPR / fix vote 6 m / 3.5 m), applied to the reported
    # position against this epoch's code-only DDPR position
    sol = np.asarray(d["sol_xyz"], dtype=float)
    ddpr = np.asarray(d["ddpr_xyz"], dtype=float)
    flt = np.asarray(d["flt_xyz"], dtype=float)
    d_fix = np.linalg.norm(sol - ddpr, axis=1)
    d_flt = np.linalg.norm(flt - ddpr, axis=1)
    has = np.isfinite(d_fix)
    guard = ~(has & ((d_fix > GUARD_M) | ((d_flt > 6.0) & (d_fix > 3.5))))
    if GUARD_M <= 0:
        guard = np.ones(len(e), dtype=bool)
    return {"e": e, "n": n, "ok": ok & guard,
            "pf_ok": np.asarray(d["rb_ok"]) == 1,
            "nb_at": np.asarray(d["rb_nb_at"]),
            "nb_at_ne": np.asarray(d["rb_nb_at_ne"]),
            "ne_mass": np.asarray(d["rb_ne_mass"]),
            "base_fix": np.asarray(d["base_smode"]) == 4,
            "base_nb": np.asarray(d["base_nb"]),
            "shipped": (np.asarray(d["smode"]) == 4) & (np.asarray(d["rb_nb"]) >= 12)}


def apply(p, t, f, t2, f2, m, b):
    fx = p["pf_ok"] & (p["nb_at"][:, t] >= f)
    fx |= p["pf_ok"] & (p["nb_at_ne"][:, t2] >= f2) & (p["ne_mass"] >= m)
    fx |= p["base_fix"] & (p["base_nb"] >= b)
    return fx & p["ok"]


def score(p, fx):
    good = fx & (p["e"] < 0.5)
    return int(good.sum()), int(fx.sum() - good.sum()), p["n"]


def describe(g):
    t, f, t2, f2, m, b = g
    s = f"nb_at[{NB_AT_THRESH[t]}]>={f}"
    if f2 < 99:
        s += f" | nb_at_ne[{NB_AT_THRESH[t2]}]>={f2}&ne_mass>={m}"
    if b < 99:
        s += f" | base_nb>={b}"
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="+")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--results", type=Path, default=_HERE / "results")
    ap.add_argument("--cap", type=float, default=0.7)
    ap.add_argument("--fixed", default=None,
                    help="t_idx,F,t2_idx,F2,M,B: score this rule on every run")
    a = ap.parse_args()
    runs = {1: [], 2: [], 3: []}
    for pre in a.prefixes:
        for r, lst in runs.items():
            path = a.results / f"{pre}_r{r}" / f"run{r}.npz"
            if path.exists():
                lst.append(prepare(dict(np.load(path)), r, a.data_root))

    def report(r, g, tag):
        rows = []
        for p in runs[r]:
            c, w, n = score(p, apply(p, *g))
            c0, w0, _ = score(p, p["shipped"] & p["ok"])
            rows.append((100 * c / n, 100 * w / max(c + w, 1),
                         100 * c0 / n, 100 * w0 / max(c0 + w0, 1)))
        v = np.asarray(rows)
        mean = v.mean(axis=0)
        sd = v.std(axis=0, ddof=1) if len(v) > 1 else np.zeros(4)
        per_seed = " ".join(f"{x[0]:.1f}/{x[1]:.2f}" for x in v)
        print(f"run{r} {tag}: correct {mean[0]:.1f}±{sd[0]:.1f}% "
              f"wrong/fix {mean[1]:.2f}% (seeds: {per_seed})  "
              f"| shipped {mean[2]:.1f}% / {mean[3]:.2f}%")

    if a.fixed:
        vals = a.fixed.split(",")
        g = (int(vals[0]), int(vals[1]), int(vals[2]), int(vals[3]),
             float(vals[4]), int(vals[5]))
        print("rule:", describe(g))
        for r, lst in runs.items():
            if lst:
                report(r, g, "fixed rule")
        return
    for held in (1, 2, 3):
        train = [r for r in runs if r != held and runs[r]]
        if not runs[held] or not train:
            continue
        best = None
        for g in GRID:
            tot = 0.0
            feasible = True
            for r in train:
                gs = ws = 0
                for p in runs[r]:
                    c, w, _ = score(p, apply(p, *g))
                    gs += c
                    ws += w
                if gs + ws and 100.0 * ws / (gs + ws) > a.cap:
                    feasible = False
                    break
                tot += gs / sum(p["n"] for p in runs[r])
            if feasible and (best is None or tot > best[0]):
                best = (tot, g)
        if best is None:
            print(f"held-out run{held}, trained on {train}: no rule meets the cap")
            continue
        print(f"held-out run{held}, trained on {train}: {describe(best[1])}")
        report(held, best[1], "held-out")


if __name__ == "__main__":
    main()
