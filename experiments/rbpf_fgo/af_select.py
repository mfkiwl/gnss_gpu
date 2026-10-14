#!/usr/bin/env python3
"""WP54/WP55: report, on epochs the FIX label leaves unfixed, the candidate
position that best fits the raw DD carrier modulo whole cycles.

The fit of a position x on one epoch's DD rows (RbpfFgo row dump: b0 is the
DD carrier minus the DD range at the float position pos0, in cycles) is the
ambiguity function

    AF(x) = mean over pairs of cos(2*pi*frac(b0 - J.(x - pos0)/lam)),

1 when every pair is an exact whole number of cycles. It needs no carrier
continuity, so cycle slips do not matter. AF is averaged over a few epochs
for each candidate, and a candidate replaces the first one (the reported
position) only when it beats it by more than `margin`.

- Post-processed (combine_fb.py, WP54): forward, backward, their mean and
  both passes' graph floats; centred window.
- Real time (wp16_run_rbpf.py --af-select N, WP55): the reported position,
  the graph float and the PF's MAP / mixture tracked beliefs; window of the
  last N epochs (`CausalSelector`).

Offline causal replay on a saved run with its row dump:

    python af_select.py results/<name>/runN.npz [--past 6] [--margin 0.1]

writes runN_af.npz (sol_xyz replaced on unfixed epochs, column pos_src).
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import numpy as np

RT_CANDS = ("sol_xyz", "flt_xyz", "trk_map_xyz", "trk_mix_xyz")
MISSING = -9.0      # AF of a candidate that cannot be evaluated


def epoch_fit(R, p0, cands):
    """(k,) AF of each candidate on one epoch's rows (NaN when missing)."""
    out = np.full(len(cands), np.nan)
    if R is None or p0 is None or len(R) == 0:
        return out
    J, lam, b0 = R[:, 7:10], R[:, 6], R[:, 10]
    for c, x in enumerate(cands):
        if x is not None and np.all(np.isfinite(x)):
            v = b0 - (J @ (np.asarray(x, dtype=float) - p0)) / lam
            out[c] = np.mean(np.cos(2 * np.pi * (v - np.rint(v))))
    return out


def ambiguity_fit(rows, cands):
    """(n, k) AF of each candidate trajectory on each epoch; rows is
    integer_smoother.load_rows output keyed by epoch index."""
    by, pos0 = rows
    return np.stack([epoch_fit(by.get(i), pos0.get(i), [c[i] for c in cands])
                     for i in range(len(cands[0]))])


def choose(af, ok, margin=0.1, half=0, past=0):
    """Index of the chosen candidate per epoch (0 = keep the first). Missing
    fits count as MISSING; `half` averages over +-half epochs, `past` over the
    last `past` epochs (zero-padded at the start, like CausalSelector)."""
    af = np.nan_to_num(af, nan=MISSING)
    if half > 0:
        ker = np.ones(2 * half + 1) / (2 * half + 1)
        af = np.stack([np.convolve(af[:, j], ker, mode="same")
                       for j in range(af.shape[1])], axis=1)
    elif past > 1:
        ker = np.ones(past) / past
        af = np.stack([np.convolve(af[:, j], ker, mode="full")[:len(af)]
                       for j in range(af.shape[1])], axis=1)
    pick = np.argmax(af, axis=1)
    pick = np.where(af[np.arange(len(pick)), pick] - af[:, 0] > margin, pick, 0)
    return np.where(ok, pick, 0)


class CausalSelector:
    """Per-epoch form of choose(..., past=N) for the live runner."""

    def __init__(self, past=6, margin=0.1):
        self.past = int(past)
        self.margin = float(margin)
        self.hist = deque(maxlen=self.past)

    def step(self, R, p0, cands, eligible):
        af = np.nan_to_num(epoch_fit(R, p0, cands), nan=MISSING)
        self.hist.append(af)
        if not eligible or not all(c is not None and np.all(np.isfinite(c))
                                   for c in cands):
            return 0
        avg = np.sum(self.hist, axis=0) / self.past
        j = int(np.argmax(avg))
        return j if avg[j] - avg[0] > self.margin else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("npz", type=Path)
    ap.add_argument("--past", type=int, default=6)
    ap.add_argument("--margin", type=float, default=0.1)
    a = ap.parse_args()
    from integer_smoother import load_rows
    d = dict(np.load(a.npz))
    rows = load_rows(a.npz.with_name(a.npz.stem + "_rows.npz"))
    cands = [np.asarray(d[c], dtype=float) for c in RT_CANDS]
    fix = np.asarray(d["rule_fix"], dtype=bool)
    ok = ~fix & np.all([np.all(np.isfinite(c), axis=1) for c in cands], axis=0)
    pick = choose(ambiguity_fit(rows, cands), ok, a.margin, past=a.past)
    d["sol_xyz"] = np.stack(cands, axis=1)[np.arange(len(pick)), pick]
    d["pos_src"] = pick
    dst = a.npz.with_name(a.npz.stem + "_af.npz")
    np.savez_compressed(dst, **d)
    print(f"{int((pick > 0).sum())} of {int((~fix).sum())} unfixed epochs "
          f"changed ({np.bincount(pick, minlength=len(RT_CANDS)).tolist()}); "
          f"wrote {dst}")


if __name__ == "__main__":
    main()
