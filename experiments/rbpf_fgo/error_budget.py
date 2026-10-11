#!/usr/bin/env python3
"""Split the `<50cm_full%` denominator of one RB-FGO-PF run into causes.

    python error_budget.py results/<name>/runN.npz N [--nb 12]

Every rover epoch (inuex35 denominator) falls in exactly one bucket:

- missing      no output epoch at all (outage, phase-1 gaps)
- fix_ok       reported FIX (after the `nb` report floor), error < 0.5 m
- fix_bad      reported FIX, error >= 0.5 m (false fix)
- flt_ok       reported FLOAT, error < 0.5 m
- flt_map_ok   FLOAT, error >= 0.5 m, but the PF's top non-empty basin
               conditional mean is < 0.5 m (a confidence problem: the right
               basin was the MAP, it was not declared)
- flt_lost     FLOAT, error >= 0.5 m, and no basin within 0.5 m
"""

from __future__ import annotations

import argparse

import numpy as np

ROVER_EPOCHS = {1: 11928, 2: 9151, 3: 15301}


def budget(npz_path, run, nb_floor=12):
    d = np.load(npz_path)
    err = np.asarray(d["err3d"], dtype=float)
    sm = np.asarray(d["smode"]).astype(int)
    nb = np.asarray(d["rb_nb"]).astype(int)
    mp = np.asarray(d["map_err3d"], dtype=float)
    fix = (sm == 4) & (nb >= nb_floor)
    ok = np.isfinite(err)
    n_full = ROVER_EPOCHS[run]
    good = ok & (err < 0.5)
    out = {
        "fix_ok": int(np.sum(fix & good)),
        "fix_bad": int(np.sum(fix & ok & ~good)),
        "flt_ok": int(np.sum(~fix & good)),
        "flt_map_ok": int(np.sum(~fix & ok & ~good & np.isfinite(mp) & (mp < 0.5))),
    }
    out["flt_lost"] = int(np.sum(ok & ~fix & ~good)) - out["flt_map_ok"]
    out["missing"] = n_full - int(np.sum(ok))
    out["n_full"] = n_full
    out["lt50_full_pct"] = 100.0 * (out["fix_ok"] + out["flt_ok"]) / n_full
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("npz")
    ap.add_argument("run", type=int, choices=(1, 2, 3))
    ap.add_argument("--nb", type=int, default=12)
    a = ap.parse_args()
    b = budget(a.npz, a.run, a.nb)
    n = b["n_full"]
    for k in ("fix_ok", "flt_ok", "fix_bad", "flt_map_ok", "flt_lost", "missing"):
        print(f"{k:11s} {b[k]:6d}  {100.0 * b[k] / n:6.2f}%")
    print(f"<50cm_full% {b['lt50_full_pct']:.2f}")


if __name__ == "__main__":
    main()
