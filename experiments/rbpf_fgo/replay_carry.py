#!/usr/bin/env python3
"""Replay the WP50 label layer (hysteresis + forward integer carry, and the
backward smoother) on a row-dump run, without re-running the filter.

    python replay_carry.py results/<src>/run1.npz results/<dst>/run1.npz

The PF never sees the label layer, so replaying it on a run made with
RBPF_DUMP_ROWS=1 and the same filter configuration gives the same output as
RBPF_SHIP=wp50 (checked on full runs); with --flt-max 0.5, the WP56 label
(RBPF_SHIP=wp56 before its WP55 position choice). Writes <dst> (causal
output) and --smooth-dst (backward carry, --max-s seconds).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from fix_rule_loro import apply, prepare
from integer_carry import HysteresisLabel, IntegerCarry, backward_carry
from integer_smoother import load_rows

ENTRY = (5, 15, 5, 15, 0.1, 5, 0, 2, 0, 1.0)     # --fix-rule 5,15,5,15,0.1,5,1.0
CONT = (2, 6, 2, 6, 0.1, 5, 0, 2, 0, 99)          # --fix-hyst 5,2,2,6,2,6,0.1,5


def replay(src, data_root, city, run, max_s=15.0, flt_max=0.0):
    d = dict(np.load(src))
    by, pos0 = load_rows(src.with_name(src.stem + "_rows.npz"))
    p = prepare(d, run, data_root, city=city)
    entry = apply(p, *ENTRY)
    cont = apply(p, *CONT)
    tow = np.asarray(d["tow"], dtype=float)
    smode = np.asarray(d["smode"])
    xyz = np.asarray(d["sol_xyz"], dtype=float).copy()
    if flt_max > 0:
        # WP56: no label while the reported position is off the graph float
        far = np.linalg.norm(xyz - np.asarray(d["flt_xyz"], dtype=float),
                             axis=1) > flt_max
        entry &= ~far
        cont &= ~far
    hyst = HysteresisLabel(entry=5, bridge=2)
    car = IntegerCarry()
    n = len(tow)
    hy = np.zeros(n, bool); cf = np.zeros(n, bool)
    for i in range(n):
        hy[i] = hyst.update(tow[i], bool(entry[i]), bool(cont[i]))
        R, p0 = by.get(i), pos0.get(i)
        if R is None or p0 is None or smode[i] == 6:
            car.reset(); car.t_prev = tow[i]
        elif hy[i]:
            car.init(R, p0, xyz[i], tow[i])
        else:
            x = car.step(R, p0, tow[i])
            if x is not None:
                cf[i] = True; xyz[i] = x
    out = dict(d)
    out["sol_xyz"] = xyz
    out["hyst_fix"] = hy.astype(int); out["carry_fwd"] = cf.astype(int)
    out["rule_fix"] = (hy | cf).astype(int)
    bwd, pb = backward_carry(by, pos0, tow, hy, xyz, max_s=max_s)
    add = bwd & ~(hy | cf) & (smode != 6)
    sm = dict(out)
    sm["sol_xyz"] = np.where(add[:, None], pb, xyz)
    sm["rule_fix"] = (hy | cf | add).astype(int)
    sm["carry_bwd"] = add.astype(int)
    return out, sm


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--city", default="tokyo")
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--max-s", type=float, default=15.0)
    ap.add_argument("--flt-max", type=float, default=0.0,
                    help="WP56 float-consistency veto [m] (0 = WP50 label)")
    ap.add_argument("--smooth-dst", type=Path, required=True)
    a = ap.parse_args()
    out, sm = replay(a.src, a.data_root, a.city, a.run, a.max_s, a.flt_max)
    a.dst.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(a.dst, **out)
    smd = a.smooth_dst
    smd.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(smd, **sm)
    print(f"{a.src}: hyst {int(out['hyst_fix'].sum())} carry_fwd {int(out['carry_fwd'].sum())} "
          f"carry_bwd {int(sm['carry_bwd'].sum())}")


if __name__ == "__main__":
    main()
