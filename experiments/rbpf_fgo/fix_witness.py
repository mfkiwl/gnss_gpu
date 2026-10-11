#!/usr/bin/env python3
"""Ambiguity-function witness study on RBPF_DUMP_ROWS runs (WP45).

    python fix_witness.py --data-root .../PPC-Dataset dp_s10

For every epoch with dumped DD rows, the candidate position (the reported
position: the MAP basin's tracked belief on FIX and FLOAT epochs alike) is
tested against every phase-observed DD row the receiver delivered:

    w = frac(b0 - J.(x - pos0)/lam)        (cycles, in [-0.5, 0.5))

A correct cm-level position puts LOS rows near an integer whatever the
ambiguity; a wrong basin (or a decimetre-level float) does not. Rows are
split into those the MAP basin holds (not independent of the candidate)
and the rest (independent witnesses, Fredeluces et al. 2024). Prints the
separation of correct vs wrong candidates on FIX and FLOAT epochs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# PPC city of the scored runs (tokyo | nagoya)
CITY = __import__("os").environ.get("PPC_CITY", "tokyo")

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent.parent / "python"))
from score_vs_inuex35 import load_reference_grid

# dumped row columns (after the leading npz row index)
RI, SYS, F, REF, SAT, ELR, ELJ, LAM, JX, JY, JZ, B0, DDPR, HELD, MASS, INP = range(16)


def epoch_stats(rows, x, pos0, thr):
    """Per-epoch witness statistics at candidate position x."""
    b = rows[:, B0] - rows[:, JX:JZ + 1] @ (x - pos0) / rows[:, LAM]
    w = np.abs(b - np.round(b))
    held = np.isfinite(rows[:, HELD])
    ok = w < thr
    # a satellite is an independent witness only when none of its rows is
    # held; it passes when every frequency it has is near an integer
    sats = {}
    for k in range(len(rows)):
        key = (int(rows[k, SYS]), int(rows[k, SAT]))
        s = sats.setdefault(key, [True, True])
        s[0] &= not held[k]
        s[1] &= bool(ok[k])
    ind = [v[1] for v in sats.values() if v[0]]
    held_match = (np.round(b[held]) == rows[held, HELD]) if held.any() else np.zeros(0, bool)
    return {
        "n": len(rows), "n_ok": int(ok.sum()),
        "n_held": int(held.sum()), "n_held_ok": int((ok & held).sum()),
        "held_match": float(held_match.mean()) if held_match.size else np.nan,
        "n_ind": len(ind), "n_ind_ok": int(sum(ind)),
        "med_w": float(np.median(w)) if len(w) else np.nan,
    }


def afv_features(npz_path, thr=0.15):
    """Truth-free per-npz-row ambiguity-function features at the reported
    position: (n rows, inlier fraction), cached next to the run as
    runN_afv<thr>.npz. Rows without a dump get n = 0, frac = nan."""
    npz_path = Path(npz_path)
    cache = npz_path.with_name(f"{npz_path.stem}_afv{thr:g}.npz")
    if cache.exists() and cache.stat().st_mtime >= npz_path.stat().st_mtime:
        c = np.load(cache)
        return c["n"], c["frac"]
    d = np.load(npz_path)
    z = np.load(npz_path.with_name(npz_path.stem + "_rows.npz"))
    sol = np.asarray(d["sol_xyz"], dtype=float)
    rows, eps = z["rows"], z["epochs"]
    rows = rows[np.argsort(rows[:, 0], kind="stable")]
    idx = rows[:, 0].astype(int)
    n = np.zeros(len(sol), dtype=int)
    frac = np.full(len(sol), np.nan)
    for e in eps:
        i = int(e[0])
        a, b = np.searchsorted(idx, i), np.searchsorted(idx, i, side="right")
        if b <= a or not np.all(np.isfinite(sol[i])):
            continue
        r = rows[a:b]
        bb = r[:, B0] - r[:, JX:JZ + 1] @ (sol[i] - e[1:4]) / r[:, LAM]
        n[i] = b - a
        frac[i] = float(np.mean(np.abs(bb - np.round(bb)) < thr))
    np.savez(cache, n=n, frac=frac)
    return n, frac


def load_run(results, prefix, run, data_root, thr):
    d = np.load(results / f"{prefix}_r{run}" / f"run{run}.npz")
    z = np.load(results / f"{prefix}_r{run}" / f"run{run}_rows.npz")
    ref = load_reference_grid(CITY, f"run{run}", data_root=data_root)
    R = np.array([ref.get(round(float(t), 1), np.full(3, np.nan)) for t in d["tow"]])
    sol = np.asarray(d["sol_xyz"], dtype=float)
    fix = (np.asarray(d["smode"]) == 4) & (np.asarray(d["rb_nb"]) >= 12)
    rows, eps = z["rows"], z["epochs"]
    order = np.argsort(rows[:, 0], kind="stable")
    rows = rows[order]
    idx = rows[:, 0].astype(int)
    starts = np.searchsorted(idx, eps[:, 0].astype(int), side="left")
    ends = np.searchsorted(idx, eps[:, 0].astype(int), side="right")
    out = []
    for k, e in enumerate(eps):
        i = int(e[0])
        x = sol[i]
        if not (np.all(np.isfinite(x)) and np.all(np.isfinite(R[i]))):
            continue
        st = epoch_stats(rows[starts[k]:ends[k]], x, e[1:4], thr)
        st.update(i=i, err=float(np.linalg.norm(x - R[i])), fix=bool(fix[i]),
                  nb12=int(np.asarray(d["rb_nb_at"])[i, 3]))
        out.append(st)
    return out, len(d["tow"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prefix")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--results", type=Path, default=_HERE / "results")
    ap.add_argument("--runs", default="1,2,3")
    ap.add_argument("--thr", type=float, default=0.15)
    a = ap.parse_args()
    for run in [int(r) for r in a.runs.split(",")]:
        st, n_ep = load_run(a.results, a.prefix, run, a.data_root, a.thr)
        E = np.array([s["err"] for s in st])
        FX = np.array([s["fix"] for s in st])
        fr = np.array([s["n_ok"] / max(s["n"], 1) for s in st])
        ind = np.array([s["n_ind"] for s in st])
        indok = np.array([s["n_ind_ok"] for s in st])
        hm = np.array([s["held_match"] for s in st])
        good = E < 0.5
        print(f"== run{run}: {len(st)} epochs with rows (npz {n_ep})")
        for lab, m in (("FIX", FX), ("FLOAT", ~FX)):
            for gl, gm in (("correct", good), ("wrong", ~good)):
                mm = m & gm
                if not mm.any():
                    continue
                q = np.nanpercentile(fr[mm], [5, 25, 50])
                print(f"  {lab:5s} {gl:7s} n={mm.sum():5d}  inlier-frac p5/25/50 "
                      f"{q[0]:.2f}/{q[1]:.2f}/{q[2]:.2f}  ind sats mean "
                      f"{ind[mm].mean():.1f} ind-fail>=1 {np.mean(ind[mm] - indok[mm] >= 1):.2f}"
                      f"  held-match {np.nanmean(hm[mm]):.3f}")
        # promotion curve on FLOAT epochs: inlier fraction threshold
        flt = ~FX
        for t in (0.7, 0.8, 0.85, 0.9, 0.95):
            for nmin in (8, 12, 16):
                n_all = np.array([s["n"] for s in st])
                sel = flt & (fr >= t) & (n_all >= nmin)
                c = int((sel & good).sum())
                w = int((sel & ~good).sum())
                print(f"  promote FLOAT: frac>={t:.2f} n>={nmin:2d}: +{c:5d} correct "
                      f"({100*c/n_ep:.1f}% of epochs) +{w:4d} wrong")
        for t in (0.5, 0.6, 0.7):
            sel = FX & (fr < t)
            print(f"  demote FIX: frac<{t:.2f}: -{int((sel & good).sum())} correct "
                  f"-{int((sel & ~good).sum())} wrong (of {int((FX & ~good).sum())})")


if __name__ == "__main__":
    main()
