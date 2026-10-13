#!/usr/bin/env python3
"""Score the RB-FGO-PF FIX label (`rule_fix`) of saved runs.

    PPC_CITY=nagoya python score_label.py --data-root .../PPC-Dataset \
        results/wp50_ng2/run2.npz [results/wp50_ng2/run2_smooth.npz ...]

Correct FIX: an epoch labelled FIX (`rule_fix`, the .pos FIX flag since
WP45; with WP50 it includes the hysteresis label and the forward integer
carry, and `integer_smoother.py` output adds the backward carry) whose 3D
error is within 0.5 m, as a share of every rover epoch. Wrong FIX: labelled
FIX with 3D error over 0.5 m, as a share of all FIX. These are the
FIX columns of docs/rbpf_fgo_report.md; `score_runs.py` gives PPC
OFFICIAL. The run number is read from the file name (run<N>...npz).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
from fix_rules import CITY, errors


def score(npz, data_root, city=CITY):
    m = re.match(r"run(\d)", Path(npz).name)
    if m is None:
        raise SystemExit(f"{npz}: expected a run<N>*.npz file name")
    d = np.load(npz)
    e, n = errors(d, int(m.group(1)), data_root, city)
    fix = np.asarray(d["rule_fix"]).astype(bool)
    ok = fix & (e <= 0.5)
    bad = fix & (e > 0.5)   # epochs without reference count as neither
    return 100.0 * ok.sum() / n, 100.0 * bad.sum() / max(int(fix.sum()), 1), int(bad.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("npz", nargs="+", type=Path)
    ap.add_argument("--data-root", type=Path, required=True)
    a = ap.parse_args()
    print(f"{'file':44s}  fix%  wrong/FIX%  (wrong epochs)")
    for p in a.npz:
        ok, wrong, nbad = score(p, a.data_root)
        print(f"{p!s:44s} {ok:5.1f}  {wrong:9.2f}  ({nbad})")


if __name__ == "__main__":
    main()
