#!/usr/bin/env python3
"""Output policies for RB-FGO-PF non-FIX epochs, scored offline (WP43).

    python output_policies.py --data-root .../PPC-Dataset sh_s10 sh_s11 sh_s12

The filter, its FIX epochs and its FIX positions are never changed; a
policy only replaces the position written on FLOAT epochs (smode 5) and
re-anchors the dead-reckoned gap-fill rows (smode 6, `--dr-fill`) that
follow them. Policies:

- base      the shared float (shipped behaviour); DR rows as recorded.
- nodr      base without the DR rows (= the shipped output).
- trk       the MAP basin's tracked position belief (RBPF_TRACK=2) when
            its particle mass is >= --trk-mass (default 0, as shipped by
            RBPF_TRACK_OUT=2), else the float. Append `+fix` to any trk
            policy to replace the FIX positions too (= RBPF_TRACK_OUT=2).
- trkv<v>   like trk, for shadow-tracking variant v (RBPF_TRACK_VARIANTS).
- trkmix    the particle-mass-weighted tracked belief of every non-empty
            particle, else the float.
- hold      causal: float + the last FIX's (fix - float) offset while the
            last FIX is at most --hold-s seconds old.
- interp    offline (uses future epochs): float + the (fix - float) offset
            interpolated in time between the FIX epochs on either side
            (one-sided near the ends), when both are within --interp-s.

The run is causal for every policy except `interp`, so `hold` and `trk`
can run in real time.
"""

from __future__ import annotations

import argparse
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


def apply_policy(d, policy, nb_floor=12, hold_s=10.0, interp_s=30.0,
                 trk_mass=0.0):
    tow = np.asarray(d["tow"], dtype=float)
    sol = np.asarray(d["sol_xyz"], dtype=float)
    flt = np.asarray(d["flt_xyz"], dtype=float)
    sm = np.asarray(d["smode"]).astype(int)
    fix = (sm == 4) & (np.asarray(d["rb_nb"]) >= nb_floor)
    out = sol.copy()
    keep = np.ones(len(tow), dtype=bool)
    if policy == "nodr":
        keep = sm != 6
        return out, fix, keep
    flt_rows = sm == 5
    if policy.endswith("+fix"):
        # also replace the FIX positions by the tracked belief
        policy = policy[:-4]
        flt_rows = (sm == 5) | (sm == 4)
    if policy == "trk":
        if "trk_map_xyz" in d and len(d["trk_map_xyz"]) == len(tow):
            t = np.asarray(d["trk_map_xyz"], dtype=float)
            m = np.asarray(d["trk_map_mass"], dtype=float)
            use = flt_rows & np.all(np.isfinite(t), axis=1) & (m >= trk_mass)
            out[use] = t[use]
    elif policy.startswith("trkv"):
        v = int(policy[4:])
        tv = np.asarray(d.get("trk_var_xyz", np.zeros((0,))), dtype=float)
        if tv.ndim == 3 and tv.shape[0] == len(tow) and v < tv.shape[1]:
            t = tv[:, v]
            m = np.asarray(d["trk_var_mass"], dtype=float)[:, v]
            use = flt_rows & np.all(np.isfinite(t), axis=1) & (m >= trk_mass)
            out[use] = t[use]
    elif policy == "trkmix":
        if "trk_mix_xyz" in d and len(d["trk_mix_xyz"]) == len(tow):
            t = np.asarray(d["trk_mix_xyz"], dtype=float)
            use = flt_rows & np.all(np.isfinite(t), axis=1)
            out[use] = t[use]
    elif policy in ("hold", "interp"):
        off = sol - flt
        last = np.full(len(tow), -1)
        nxt = np.full(len(tow), -1)
        cur = -1
        for i in range(len(tow)):
            if fix[i]:
                cur = i
            last[i] = cur
        cur = -1
        for i in range(len(tow) - 1, -1, -1):
            if fix[i]:
                cur = i
            nxt[i] = cur
        for i in np.where(flt_rows)[0]:
            a, b = last[i], nxt[i]
            okA = a >= 0 and tow[i] - tow[a] <= (hold_s if policy == "hold" else interp_s)
            okB = policy == "interp" and b >= 0 and tow[b] - tow[i] <= interp_s
            if okA and okB:
                w = (tow[i] - tow[a]) / max(tow[b] - tow[a], 1e-9)
                out[i] = flt[i] + (1 - w) * off[a] + w * off[b]
            elif okA:
                out[i] = flt[i] + off[a]
            elif okB:
                out[i] = flt[i] + off[b]
    # DR rows ride on the last written non-DR row
    shift = np.zeros(3)
    for i in range(len(tow)):
        if sm[i] == 6:
            out[i] = sol[i] + shift
        else:
            shift = out[i] - sol[i]
    return out, fix, keep


def score(d, run, ref, xyz, fix, keep):
    epochs = [TrajectoryEpoch(tow=float(t), ecef=x, is_fix=bool(f))
              for t, x, f, k in zip(d["tow"], xyz, fix, keep) if k]
    return score_trajectory(epochs, ref, city="tokyo", run=f"run{run}",
                            traj_path=Path("."), fmt="npz")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="+")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--results", type=Path, default=_HERE / "results")
    ap.add_argument("--policies", default="nodr,base,trk,hold,interp")
    ap.add_argument("--hold-s", type=float, default=10.0)
    ap.add_argument("--interp-s", type=float, default=30.0)
    ap.add_argument("--trk-mass", type=float, default=0.0)
    a = ap.parse_args()
    pols = a.policies.split(",")
    refs = {r: load_reference_grid("tokyo", f"run{r}", data_root=a.data_root)
            for r in (1, 2, 3)}
    agg = defaultdict(lambda: defaultdict(list))
    print(f"{'prefix':14s} run " + " ".join(f"{p:>15s}" for p in pols)
          + "   (<50cm_full% / OFFICIAL)")
    for pre in a.prefixes:
        for run in (1, 2, 3):
            npz = a.results / f"{pre}_r{run}" / f"run{run}.npz"
            if not npz.exists():
                continue
            d = dict(np.load(npz))
            cells = []
            for pol in pols:
                xyz, fix, keep = apply_policy(d, pol, hold_s=a.hold_s,
                                              interp_s=a.interp_s,
                                              trk_mass=a.trk_mass)
                r = score(d, run, refs[run], xyz, fix, keep)
                cells.append(f"{r.lt50cm_full_pct:6.2f}/{r.ppc_official_pct:6.2f}")
                agg[pol][run].append((r.lt50cm_full_pct, r.ppc_official_pct))
            print(f"{pre:14s} {run}   " + " ".join(f"{c:>15s}" for c in cells))
    print()
    for pol in pols:
        row = []
        for run in (1, 2, 3):
            v = np.asarray(agg[pol][run])
            if len(v):
                row.append(f"run{run} {v[:, 0].mean():6.2f}/{v[:, 1].mean():6.2f} (n={len(v)})")
        print(f"{pol:8s} mean  " + "   ".join(row))


if __name__ == "__main__":
    main()
