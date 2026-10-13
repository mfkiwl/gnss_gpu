#!/usr/bin/env python3
"""WP51: post-processed RB-FGO-PF output from a forward and a backward pass.

    python combine_fb.py results/<fwd>/runN.npz results/<bwd>/runN.npz

The forward run is a WP50 run with its row dump (runN_rows.npz,
RBPF_DUMP_ROWS=1). The backward run is the same configuration on the
time-mirrored run (wp16_run_rbpf.py --reverse 1). All steps work on the
forward run's epochs and DD rows:

1. backward integer carry from the forward label's hysteresis fixes, with
   no lag limit (integer_carry.backward_carry);
2. anchored union: a contiguous backward FIX segment (gaps up to 0.6 s)
   adds its epochs only when the step-1 label is FIX within --d of it on at
   least --k of its epochs and never further. Backward fixes are never used
   on their own: both passes can lock the same wrong basin (Nagoya 2), so
   agreement is required with a label that already holds. Where the two
   labels disagree, step 1 wins;
3. backward and forward integer carry from every FIX epoch of step 2.

Writes runN_fb.npz (sol_xyz and rule_fix replaced; column fb_src: 0 float,
1 forward label, 2 step-1 carry, 3 backward PF, 4 step-3 carry) and
runN_fb.pos next to the forward run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from integer_carry import backward_carry, forward_carry
from integer_smoother import load_rows


def _merge(fix, xyz, src, add, pos, code):
    add = add & ~fix
    xyz[add] = pos[add]
    src[add] = code
    return fix | add


def combine(fwd, bwd, rows, k=5, d=0.3):
    """fwd / bwd: dicts of the two runs' npz columns; rows: the forward row
    dump (load_rows). Returns (fix, xyz, src) on the forward epochs."""
    by, pos0 = rows
    tow = np.asarray(fwd["tow"], dtype=float)
    smode = np.asarray(fwd["smode"])
    usable = smode != 6
    fix = np.asarray(fwd["rule_fix"], dtype=bool).copy()
    xyz = np.asarray(fwd["sol_xyz"], dtype=float).copy()
    src = fix.astype(int)
    hyst = np.asarray(fwd.get("hyst_fix", fwd["rule_fix"]), dtype=bool)

    # 1) backward carry from the forward label, no lag limit
    c, p = backward_carry(by, pos0, tow, hyst, xyz, max_s=np.inf)
    fix = _merge(fix, xyz, src, c & usable, p, 2)

    # 2) anchored union with the backward pass
    key = {round(float(t), 1): i for i, t in enumerate(tow)}
    bfix = np.zeros(len(tow), dtype=bool)
    bxyz = np.full((len(tow), 3), np.nan)
    for t, f, x in zip(bwd["tow"], bwd["rule_fix"], bwd["sol_xyz"]):
        i = key.get(round(float(t), 1))
        if i is not None:
            bfix[i] = bool(f)
            bxyz[i] = x
    dist = np.linalg.norm(xyz - bxyz, axis=1)
    agree = fix & bfix & (dist < d)
    clash = fix & bfix & ~(dist < d)
    add = np.zeros(len(tow), dtype=bool)
    idx = np.where(bfix)[0]
    if len(idx):
        for seg in np.split(idx, np.where(np.diff(tow[idx]) > 0.65)[0] + 1):
            if agree[seg].sum() >= k and not clash[seg].any():
                add[seg] = True
    fix = _merge(fix, xyz, src, add & usable, bxyz, 3)

    # 3) carry both ways from every FIX epoch
    c, p = backward_carry(by, pos0, tow, fix, xyz, max_s=np.inf)
    fix = _merge(fix, xyz, src, c & usable, p, 4)
    c, p = forward_carry(by, pos0, tow, fix, xyz)
    fix = _merge(fix, xyz, src, c & usable, p, 4)
    return fix, xyz, src


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fwd", type=Path)
    ap.add_argument("bwd", type=Path)
    ap.add_argument("--k", type=int, default=5,
                    help="agreeing epochs a backward FIX segment needs")
    ap.add_argument("--d", type=float, default=0.3,
                    help="agreement distance [m]")
    a = ap.parse_args()
    fwd = dict(np.load(a.fwd))
    bwd = dict(np.load(a.bwd))
    rows = load_rows(a.fwd.with_name(a.fwd.stem + "_rows.npz"))
    fix, xyz, src = combine(fwd, bwd, rows, a.k, a.d)
    out = dict(fwd)
    out["sol_xyz"] = xyz
    out["rule_fix"] = fix.astype(int)
    out["fb_src"] = src
    dst = a.fwd.with_name(a.fwd.stem + "_fb.npz")
    np.savez_compressed(dst, **out)
    from wp13a_run_standalone_rtk import write_pos_file
    pos = [(int(w), float(t), x, 4 if f else 5, 0)
           for w, t, x, f in zip(out["week"], out["tow"], xyz, fix)]
    write_pos_file(dst.with_suffix(".pos"), pos,
                   f"WP51 RB-FGO-PF forward + backward pass ({a.fwd.name}, "
                   f"{a.bwd.name}), anchored union k={a.k} d={a.d:g} m")
    n = np.bincount(src, minlength=5)
    print(f"FIX {int(fix.sum())}: forward {n[1]}, carry {n[2]}, backward PF "
          f"{n[3]}, carry from union {n[4]}; wrote {dst}")


if __name__ == "__main__":
    main()
