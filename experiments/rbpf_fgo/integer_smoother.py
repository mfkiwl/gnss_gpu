#!/usr/bin/env python3
"""WP50 backward integer carry (fixed-lag / post-processed output).

    python integer_smoother.py results/<name>/run1.npz [--max-s 15]

Reads a WP50 run (run{N}.npz with `hyst_fix`, and the row dump
run{N}_rows.npz from RBPF_DUMP_ROWS=1) and carries each FIX epoch's integer
set backward in time for up to --max-s seconds, while the raw DD carrier
keeps agreeing with it (integer_carry.backward_carry). Epochs it reaches
that the causal output left unfixed are labelled FIX at the carried
position. Writes run{N}_smooth.npz (sol_xyz / rule_fix replaced, plus a
`carry_bwd` column) and run{N}_smooth.pos. With --max-s D this is the
output of a D-second fixed-lag smoother: nothing later than t + D is used.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from integer_carry import backward_carry


def load_rows(path):
    z = np.load(path)
    rows, ep = z["rows"], z["epochs"]
    order = np.argsort(rows[:, 0], kind="stable")
    rows = rows[order]
    idx = rows[:, 0].astype(int)
    by = {}
    if len(idx):
        starts = np.r_[0, np.where(np.diff(idx))[0] + 1]
        ends = np.r_[starts[1:], len(idx)]
        for a, b in zip(starts, ends):
            by[int(idx[a])] = rows[a:b, 1:]
    pos0 = {int(e[0]): e[1:4] for e in ep}
    return by, pos0


def smooth(npz_path, max_s=15.0):
    d = dict(np.load(npz_path))
    rows_path = npz_path.with_name(npz_path.stem + "_rows.npz")
    by, pos0 = load_rows(rows_path)
    tow = np.asarray(d["tow"], dtype=float)
    src = np.asarray(d.get("hyst_fix", d["rule_fix"]), dtype=bool)
    xyz = np.asarray(d["sol_xyz"], dtype=float)
    smode = np.asarray(d["smode"])
    bwd, pos = backward_carry(by, pos0, tow, src, xyz, max_s=max_s)
    add = bwd & ~np.asarray(d["rule_fix"], dtype=bool) & (smode != 6)
    d["sol_xyz"] = np.where(add[:, None], pos, xyz)
    d["rule_fix"] = (np.asarray(d["rule_fix"], dtype=bool) | add).astype(int)
    d["carry_bwd"] = add.astype(int)
    return d, add


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("npz", type=Path)
    ap.add_argument("--max-s", type=float, default=15.0)
    a = ap.parse_args()
    d, add = smooth(a.npz, a.max_s)
    out = a.npz.with_name(a.npz.stem + "_smooth.npz")
    np.savez_compressed(out, **d)
    from wp13a_run_standalone_rtk import write_pos_file
    rows = [(int(w), float(t), x, 4 if f else 5, 0)
            for w, t, x, f in zip(d["week"], d["tow"], d["sol_xyz"], d["rule_fix"])]
    write_pos_file(out.with_suffix(".pos"), rows,
                   f"WP50 RB-FGO-PF + backward integer carry (fixed lag "
                   f"{a.max_s:g} s), from {a.npz.name}")
    print(f"backward carry: +{int(add.sum())} FIX epochs; wrote {out}")


if __name__ == "__main__":
    main()
