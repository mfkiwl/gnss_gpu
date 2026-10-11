#!/usr/bin/env python3
"""Offline FIX-rule sweep on runs that carry the WP44 `rb_nb_at` diagnostic.

    python fix_rules.py --data-root .../PPC-Dataset fx_s10 [fx_s11 ...]

A rule (t, F) labels an epoch FIX when at least F of the MAP basin's pairs
have particle agreement mass above t (thresholds in rbpf_fgo.NB_AT_THRESH).
The shipped rule is t = 0.99 with F = 12. Positions are the run's reported
ones, so only labels change. With --phase1, phase-1 epochs where the shared
filter's own fix-and-hold AR reported FIX (`base_smode` 4, `base_nb` >= F)
are labelled FIX too. Prints correct-FIX % of rover epochs and wrong/FIX %.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))
from score_vs_inuex35 import load_reference_grid, rover_epoch_count

from rbpf_fgo import NB_AT_THRESH


def errors(d, run, data_root):
    ref = load_reference_grid("tokyo", f"run{run}", data_root=data_root)
    R = np.array([ref.get(round(float(t), 1), np.full(3, np.nan)) for t in d["tow"]])
    return np.linalg.norm(np.asarray(d["sol_xyz"]) - R, axis=1), rover_epoch_count(
        "tokyo", f"run{run}", ref)


def labels(d, t_idx, floor, phase1):
    nb_at = np.asarray(d["rb_nb_at"])
    sm = np.asarray(d["smode"])
    fix = (sm != 6) & (nb_at[:, t_idx] >= floor) & (np.asarray(d["rb_ok"]) == 1)
    if phase1 and "base_smode" in d:
        p1 = np.asarray(d["ar_phase"]) == 1
        fix |= p1 & (np.asarray(d["base_smode"]) == 4) & (np.asarray(d["base_nb"]) >= floor)
    return fix


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="+")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--results", type=Path, default=_HERE / "results")
    ap.add_argument("--floors", default="5,7,9,12,15")
    ap.add_argument("--phase1", action="store_true")
    a = ap.parse_args()
    floors = [int(x) for x in a.floors.split(",")]
    for pre in a.prefixes:
        for run in (1, 2, 3):
            p = a.results / f"{pre}_r{run}" / f"run{run}.npz"
            if not p.exists():
                continue
            d = dict(np.load(p))
            e, n = errors(d, run, a.data_root)
            print(f"{pre} run{run}   (correct% / wrong-of-fix%)")
            print("   t\\F  " + "".join(f"{f:>15d}" for f in floors))
            for ti, t in enumerate(NB_AT_THRESH):
                cells = []
                for f in floors:
                    fx = labels(d, ti, f, a.phase1)
                    ok = fx & np.isfinite(e)
                    good = ok & (e < 0.5)
                    cells.append(f"{100 * good.sum() / n:6.1f}/{100 * (ok.sum() - good.sum()) / max(ok.sum(), 1):5.2f}")
                print(f"  {t:4.2f}  " + "".join(f"{c:>15s}" for c in cells))


if __name__ == "__main__":
    main()
