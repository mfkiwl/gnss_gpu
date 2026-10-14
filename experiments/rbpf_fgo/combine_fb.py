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
3. backward and forward integer carry from every FIX epoch of step 2;
4. (WP54) on epochs still unfixed, the reported position is the candidate
   that best fits the raw DD carrier modulo whole cycles: forward, backward,
   their mean, or either pass's graph float. The fit is the ambiguity
   function mean(cos 2*pi*frac(b0 - J.(x - pos0)/lam)) over the forward
   rows, averaged over +-`--af-half` epochs; the forward position stays
   unless another candidate beats it by more than `--af-margin`. FIX labels
   do not change. `--af-half -1` skips this step.

Writes runN_fb.npz (sol_xyz and rule_fix replaced; column fb_src: 0 float,
1 forward label, 2 step-1 carry, 3 backward PF, 4 step-3 carry; column
pos_src on unfixed epochs: 0 forward, 1 backward, 2 mean, 3 forward float,
4 backward float) and runN_fb.pos next to the forward run.
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


def _on_fwd(tow, btow, bval):
    """Backward-pass column on the forward epochs (NaN where absent)."""
    key = {round(float(t), 1): i for i, t in enumerate(tow)}
    out = np.full((len(tow),) + np.shape(bval)[1:], np.nan)
    for t, v in zip(btow, bval):
        i = key.get(round(float(t), 1))
        if i is not None:
            out[i] = v
    return out


def ambiguity_fit(rows, cands):
    """(n, k) ambiguity-function value of each candidate position on each
    epoch's DD rows (NaN without rows or position)."""
    by, pos0 = rows
    n, k = len(cands[0]), len(cands)
    af = np.full((n, k), np.nan)
    for i in range(n):
        R, p0 = by.get(i), pos0.get(i)
        if R is None or p0 is None:
            continue
        J, lam, b0 = R[:, 7:10], R[:, 6], R[:, 10]
        for c in range(k):
            x = cands[c][i]
            if np.all(np.isfinite(x)):
                v = b0 - (J @ (x - p0)) / lam
                af[i, c] = np.mean(np.cos(2 * np.pi * (v - np.rint(v))))
    return af


def select_float(fwd, bwd, rows, fix, xyz, half=5, margin=0.1):
    """WP54 step 4. Returns (xyz, pos_src)."""
    tow = np.asarray(fwd["tow"], dtype=float)
    bx = _on_fwd(tow, bwd["tow"], np.asarray(bwd["sol_xyz"], dtype=float))
    bf = _on_fwd(tow, bwd["tow"], np.asarray(bwd["flt_xyz"], dtype=float))
    cands = [xyz, bx, 0.5 * (xyz + bx), np.asarray(fwd["flt_xyz"], dtype=float), bf]
    ok = ~fix & np.all([np.all(np.isfinite(c), axis=1) for c in cands], axis=0)
    af = np.nan_to_num(ambiguity_fit(rows, cands), nan=-9.0)
    if half > 0:
        ker = np.ones(2 * half + 1) / (2 * half + 1)
        af = np.stack([np.convolve(af[:, j], ker, mode="same")
                       for j in range(af.shape[1])], axis=1)
    pick = np.argmax(af, axis=1)
    pick = np.where(af[np.arange(len(pick)), pick] - af[:, 0] > margin, pick, 0)
    pick = np.where(ok, pick, 0)
    out = np.stack(cands, axis=1)[np.arange(len(pick)), pick]
    return np.where(ok[:, None], out, xyz), pick


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
    ap.add_argument("--af-half", type=int, default=5,
                    help="WP54: ambiguity-fit averaging half-window [epochs]; "
                         "-1 keeps the forward float positions")
    ap.add_argument("--af-margin", type=float, default=0.1,
                    help="WP54: fit margin a candidate needs over the forward "
                         "position")
    a = ap.parse_args()
    fwd = dict(np.load(a.fwd))
    bwd = dict(np.load(a.bwd))
    rows = load_rows(a.fwd.with_name(a.fwd.stem + "_rows.npz"))
    fix, xyz, src = combine(fwd, bwd, rows, a.k, a.d)
    pos_src = np.zeros(len(fix), dtype=int)
    if a.af_half >= 0:
        xyz, pos_src = select_float(fwd, bwd, rows, fix, xyz, a.af_half, a.af_margin)
    out = dict(fwd)
    out["sol_xyz"] = xyz
    out["rule_fix"] = fix.astype(int)
    out["fb_src"] = src
    out["pos_src"] = pos_src
    dst = a.fwd.with_name(a.fwd.stem + "_fb.npz")
    np.savez_compressed(dst, **out)
    from wp13a_run_standalone_rtk import write_pos_file
    pos = [(int(w), float(t), x, 4 if f else 5, 0)
           for w, t, x, f in zip(out["week"], out["tow"], xyz, fix)]
    write_pos_file(dst.with_suffix(".pos"), pos,
                   f"WP51 RB-FGO-PF forward + backward pass "
                   f"({a.fwd.parent.name}/{a.fwd.name}, "
                   f"{a.bwd.parent.name}/{a.bwd.name}), anchored union "
                   f"k={a.k} d={a.d:g} m")
    n = np.bincount(src, minlength=5)
    m = np.bincount(pos_src[~fix], minlength=5)
    print(f"FIX {int(fix.sum())}: forward {n[1]}, carry {n[2]}, backward PF "
          f"{n[3]}, carry from union {n[4]}; unfixed positions: forward "
          f"{m[0]}, backward {m[1]}, mean {m[2]}, forward float {m[3]}, "
          f"backward float {m[4]}; wrote {dst}")


if __name__ == "__main__":
    main()
