#!/usr/bin/env python3
"""Score RB-FGO-PF result directories with the official matched-3D scorer.

    python score_runs.py --data-root .../PPC-Dataset base_s10 base_s11 ...

Each prefix P is scored on results/P_r{1,2,3}/run{N}.npz (missing runs are
skipped). Fixes below the `nb` report floor are relabelled float, exactly
as `relabel_nb9_pos.py` does for the shipped tables. Prints one row per
(prefix, run) and, when several prefixes share a variant name before
`_s<seed>`, the per-run mean and sample std over seeds.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from score_vs_inuex35 import (
    TrajectoryEpoch,
    load_reference_grid,
    score_trajectory,
)


def score_npz(npz, run, data_root, nb_floor, drop_dr=False):
    d = np.load(npz)
    sm = np.asarray(d["smode"])
    fix = (sm == 4) & (np.asarray(d["rb_nb"]) >= nb_floor)
    epochs = [TrajectoryEpoch(tow=float(t), ecef=np.asarray(x, dtype=float),
                              is_fix=bool(f))
              for t, x, f, s in zip(d["tow"], d["sol_xyz"], fix, sm)
              if not (drop_dr and s == 6)]
    ref = load_reference_grid("tokyo", f"run{run}", data_root=data_root)
    res = score_trajectory(epochs, ref, city="tokyo", run=f"run{run}",
                           traj_path=Path(npz), fmt="npz")
    # false-fix share among reported fixes
    ref_tows = np.asarray(sorted(ref))
    bad = 0
    for e in epochs:
        if not e.is_fix:
            continue
        i = int(np.argmin(np.abs(ref_tows - round(e.tow, 1))))
        if abs(ref_tows[i] - round(e.tow, 1)) <= 0.05:
            bad += int(np.linalg.norm(e.ecef - ref[ref_tows[i]]) >= 0.5)
    false_pct = 100.0 * bad / res.n_fix if res.n_fix else 0.0
    return res, false_pct


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="+")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--nb", type=int, default=12)
    ap.add_argument("--results", type=Path, default=_HERE / "results")
    ap.add_argument("--drop-dr", action="store_true",
                    help="ignore --dr-fill rows (smode 6), i.e. score the "
                         "same run without the gap fill")
    a = ap.parse_args()
    groups = defaultdict(lambda: defaultdict(list))
    print(f"{'prefix':28s} run  fix%   FixRMS  false%  <50full%  OFFICIAL")
    for pre in a.prefixes:
        for run in (1, 2, 3):
            npz = a.results / f"{pre}_r{run}" / f"run{run}.npz"
            if not npz.exists():
                continue
            res, fp = score_npz(npz, run, a.data_root, a.nb, a.drop_dr)
            fr = float("nan") if res.fix_rms_m is None else res.fix_rms_m
            print(f"{pre:28s} {run}  {res.fix_pct:5.1f}  {fr:6.3f}  {fp:5.2f}"
                  f"  {res.lt50cm_full_pct:7.2f}  {res.ppc_official_pct:7.2f}")
            var = re.sub(r"_s\d+$", "", pre)
            groups[var][run].append((res.lt50cm_full_pct, res.ppc_official_pct,
                                     fp, fr, res.fix_pct))
    print()
    for var, runs in groups.items():
        for run in sorted(runs):
            v = np.asarray(runs[run])
            if len(v) < 2:
                continue
            m, s = v.mean(axis=0), v.std(axis=0, ddof=1)
            print(f"{var:20s} run{run} n={len(v)}  <50full {m[0]:6.2f}±{s[0]:4.2f}"
                  f"  OFFICIAL {m[1]:6.2f}±{s[1]:4.2f}  false% {m[2]:5.2f}"
                  f"  FixRMS {m[3]:.3f}  fix% {m[4]:5.1f}")


if __name__ == "__main__":
    main()
