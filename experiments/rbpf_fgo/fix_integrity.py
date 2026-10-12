#!/usr/bin/env python3
"""FIX-rate / wrong-FIX comparison on Tokyo PPC with one scorer (WP44).

    python fix_integrity.py --data-root .../PPC-Dataset \\
        --traj ours:1:results/fin_s10_r1/run1.npz:rbpf \\
        --traj inuex35:1:results/inuex35/run1.npz:npz \\
        --traj libgnss++:1:results/libgnsspp/run1.pos:pos

Each `--traj label:run:path:format` (format rbpf | npz | pos | tc | lcsv) is matched to
the 5 Hz reference within 0.05 s, as in `score_vs_inuex35.py`. A FIX is
wrong when its 3D error is >= 0.5 m. For `rbpf` files the FIX label uses
the `nb >= --nb` report floor. `tc` is inuex35's SAVE_NPZ, scored against
the `truth_xyz` it stores per rover epoch. `lcsv` is libgnss++'s
`gnss_fgo_parity --dump-shadow-csv` (status 4 = FIXED). Columns:

- fix%        FIX epochs / rover epochs (the inuex35 denominator)
- correct%    correct FIX epochs / rover epochs
- wrong/fix%  wrong FIX epochs / FIX epochs
- dCorrect%   correct FIX share of the travelled reference distance
- dWrong%     wrong FIX share of the travelled reference distance
              (the two distance columns follow libgnss++'s `fgo-tokyo`
              table)
- FixRMS      3D RMS over FIX epochs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# PPC city of the scored runs (tokyo | nagoya)
CITY = __import__("os").environ.get("PPC_CITY", "tokyo")

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent.parent / "python"))
from gnss_gpu.ppc_score import ppc_segment_distances
from score_vs_inuex35 import (
    _lookup_reference_tow,
    load_npz_trajectory,
    load_pos_trajectory,
    load_reference_grid,
    rover_epoch_count,
)


def load(path, fmt, nb_floor):
    if fmt == "rbpf":
        d = np.load(path)
        fix = (np.asarray(d["smode"]) == 4) & (np.asarray(d["rb_nb"]) >= nb_floor)
        return [(float(t), np.asarray(x, dtype=float), bool(f))
                for t, x, f in zip(d["tow"], d["sol_xyz"], fix)]
    if fmt == "lcsv":
        # libgnss++ gnss_fgo_parity --dump-shadow-csv (status 4 = FIXED)
        import csv
        out = []
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                xyz = np.array([float(row["x_ecef_m"]), float(row["y_ecef_m"]),
                                float(row["z_ecef_m"])])
                out.append((float(row["tow"]), xyz, int(row["status"]) == 4))
        return out
    eps = (load_npz_trajectory(Path(path)) if fmt == "npz"
           else load_pos_trajectory(Path(path)))
    return [(e.tow, e.ecef, e.is_fix) for e in eps]


def integrity_tc(path, run, data_root):
    """inuex35 SAVE_NPZ: one row per rover epoch with its own aligned
    `truth_xyz` (no tow), so errors and distance weights come from it."""
    d = np.load(path)
    sol = np.asarray(d["sol_xyz"], dtype=float)
    truth = np.asarray(d["truth_xyz"], dtype=float)
    fix = np.asarray(d["smode"]).astype(int) == 4
    ok = np.all(np.isfinite(truth), axis=1) & np.all(np.isfinite(sol), axis=1)
    seg = np.zeros(len(sol))
    tv = np.where(ok[:, None], truth, np.nan)
    step = np.linalg.norm(np.diff(tv, axis=0), axis=1)
    seg[1:] = np.where(np.isfinite(step) & (step < 10.0), step, 0.0)
    ref = load_reference_grid(CITY, f"run{run}", data_root=data_root)
    ref_xyz = np.asarray([ref[t] for t in sorted(ref)])
    total = float(np.nansum(ppc_segment_distances(ref_xyz)))
    n_rover = rover_epoch_count(CITY, f"run{run}", ref)
    e = np.linalg.norm(sol - truth, axis=1)
    f = fix & ok
    good = f & (e < 0.5)
    return {
        "fix": 100.0 * f.sum() / n_rover,
        "correct": 100.0 * good.sum() / n_rover,
        "wrong_of_fix": 100.0 * (f.sum() - good.sum()) / max(f.sum(), 1),
        "d_correct": 100.0 * seg[good].sum() / total,
        "d_wrong": 100.0 * seg[f & ~good].sum() / total,
        "fix_rms": float(np.sqrt(np.mean(e[f] ** 2))) if f.any() else float("nan"),
    }


def integrity(epochs, run, data_root):
    ref = load_reference_grid(CITY, f"run{run}", data_root=data_root)
    ref_tows = np.asarray(sorted(ref))
    ref_xyz = np.asarray([ref[t] for t in ref_tows])
    seg = ppc_segment_distances(ref_xyz)
    seg_by_tow = dict(zip(ref_tows.tolist(), seg.tolist()))
    total = float(np.nansum(seg))
    n_rover = rover_epoch_count(CITY, f"run{run}", ref)
    n_fix = n_ok = 0
    d_ok = d_bad = 0.0
    errs = []
    seen = set()
    for tow, xyz, is_fix in epochs:
        rt = _lookup_reference_tow(tow, ref_tows)
        if rt is None or rt in seen or not is_fix or not np.all(np.isfinite(xyz)):
            continue
        seen.add(rt)
        e = float(np.linalg.norm(xyz - ref[rt]))
        n_fix += 1
        errs.append(e)
        w = seg_by_tow.get(rt, 0.0)
        w = 0.0 if not np.isfinite(w) else w
        if e < 0.5:
            n_ok += 1
            d_ok += w
        else:
            d_bad += w
    errs = np.asarray(errs)
    return {
        "fix": 100.0 * n_fix / n_rover,
        "correct": 100.0 * n_ok / n_rover,
        "wrong_of_fix": 100.0 * (n_fix - n_ok) / n_fix if n_fix else float("nan"),
        "d_correct": 100.0 * d_ok / total,
        "d_wrong": 100.0 * d_bad / total,
        "fix_rms": float(np.sqrt(np.mean(errs ** 2))) if errs.size else float("nan"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traj", action="append", required=True,
                    help="label:run:path:format (format rbpf | npz | pos | tc | lcsv)")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--nb", type=int, default=12)
    a = ap.parse_args()
    print(f"{'label':22s} run   fix%  correct%  wrong/fix%  dCorrect%  dWrong%  FixRMS")
    for spec in a.traj:
        label, run, path, fmt = spec.split(":", 3)
        r = (integrity_tc(path, int(run), a.data_root) if fmt == "tc"
             else integrity(load(path, fmt, a.nb), int(run), a.data_root))
        print(f"{label:22s} {run:>3s}  {r['fix']:5.1f}  {r['correct']:7.1f}  "
              f"{r['wrong_of_fix']:9.2f}  {r['d_correct']:8.1f}  {r['d_wrong']:7.2f}"
              f"  {r['fix_rms']:6.3f}")


if __name__ == "__main__":
    main()
