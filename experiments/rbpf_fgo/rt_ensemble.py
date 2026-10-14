#!/usr/bin/env python3
"""WP59/WP60: real-time ensemble of RB-FGO-PF runs, scored the PPC2024 way.

Each member is a forward (real-time) run on the same epochs; every epoch
offers four candidates per member (reported position, graph float, PF MAP,
PF mixture). Per epoch the output is chosen causally, from this epoch and
earlier ones only:

- ``--select af`` (WP55 rule): a member's reported FIX first (best windowed
  ambiguity fit among the fixing members), else the candidate with the best
  ambiguity fit over the last N epochs when it beats member 0 by a margin.
- ``--select learned``: a gradient-boosted classifier scores every
  candidate's chance of being within 0.5 m from causal features (windowed
  ambiguity fit, FIX flags, agreement between candidates, PF state, time
  since FIX); the best one is output. Trained leave-one-run-out: the model
  for a run never sees that run's ground truth.

Then, optionally:

- ``--shift``: non-FIX outputs move down by a height bias chosen
  leave-one-run-out from 0-0.3 m (the float solution sits high in street
  canyons, where delayed reflected signals lift it);
- ``--gaps PATTERN``: rover-gap dead-reckoning rows (wp16_run_rbpf.py
  ``--gap-fill``) are added from that member, anchored on the output.

The FIX label is FIX where at least ``--label-votes`` of the
``--label-members`` hold their own label within 0.2 m of the output.

PPC OFFICIAL: distance share (|v_ref| * dt; stops do not count) of reference
epochs within 0.5 m (3D), missing epochs failing, averaged over the runs.

    python rt_ensemble.py --select learned --shift --gaps 'on3_{ct}{r}' \\
        --members 'af56{CT}_s10_r{r}' 'af56{CT}_s11_r{r}' 'af56{CT}_s12_r{r}' \\
                  'w58b_{ct}{r}' 'on3_{ct}{r}' \\
        --label-members 'af56{CT}_s10_r{r}' 'af56{CT}_s11_r{r}' \\
                        'af56{CT}_s12_r{r}' 'w58b_{ct}{r}'
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from af_select import MISSING, epoch_fit
from fix_rules import errors
from integer_smoother import load_rows

CANDS = ("sol_xyz", "flt_xyz", "trk_map_xyz", "trk_mix_xyz")
NC = len(CANDS)
CITY = {"tk": "tokyo", "ng": "nagoya"}
RUNS = [(c, r) for c in ("tk", "ng") for r in (1, 2, 3)]
MEMBER_KEYS = ("hyst_fix", "carry_fwd", "nsat_dd", "rb_npairs", "rb_nb",
               "trk_map_mass", "rb_ess", "gamma", "rb_ddpr_qual",
               "rb_rep_dpr", "rb_min_info", "smode", "rb_nbasins")


def fmt(pat, ct, r):
    return pat.format(ct=ct, CT=ct.upper(), r=r)


def reference(data_root, ct, r):
    a = np.genfromtxt(Path(data_root) / CITY[ct] / f"run{r}" / "reference.csv",
                      delimiter=",", skip_header=1)
    tow = np.round(a[:, 0], 1)
    dt = np.r_[np.diff(a[:, 0]), 0.2]
    dt = np.where((dt > 0) & (dt < 1.0), dt, 0.2)
    return tow, a[:, 5:8], np.linalg.norm(a[:, 11:14], axis=1) * dt


def per_epoch_ok(ref, tow, pos, thr=0.5):
    rt, x, w = ref
    key = {round(float(t), 1): i for i, t in enumerate(tow)}
    idx = np.array([key.get(float(t), -1) for t in rt])
    p = np.full_like(x, np.nan)
    p[idx >= 0] = pos[idx[idx >= 0]]
    e = np.linalg.norm(p - x, axis=1)
    return np.isfinite(e) & (e <= thr), w


def official(ref, tow, pos):
    ok, w = per_epoch_ok(ref, tow, pos)
    return 100.0 * w[ok].sum() / w.sum()


def with_gaps(tow, pos, gap_rows):
    """Add rover-gap DR rows [tow, anchor tow, xyz, dxyz]: the output at the
    anchor epoch plus the IMU antenna delta (else the member's own xyz)."""
    if gap_rows is None or len(gap_rows) == 0:
        return tow, pos
    key = {round(float(t), 1): i for i, t in enumerate(tow)}
    add_t, add_p = [], []
    for g in gap_rows:
        if round(float(g[0]), 1) in key:
            continue
        i = key.get(round(float(g[1]), 1))
        ok = i is not None and np.all(np.isfinite(pos[i]))
        add_t.append(g[0])
        add_p.append(pos[i] + g[5:8] if ok else g[2:5])
    if not add_t:
        return tow, pos
    t2 = np.r_[tow, add_t]
    p2 = np.vstack([pos, np.asarray(add_p)])
    o = np.argsort(t2, kind="stable")
    return t2[o], p2[o]


class Run:
    """Members, candidates, FIX flags and per-candidate ambiguity fits of one
    PPC run (the fits use one row dump, so every member is scored on the
    same DD carrier rows)."""

    def __init__(self, results, rows_pat, cache, ct, r, members):
        self.ct, self.r = ct, r
        self.names = [fmt(m, ct, r) for m in members]
        self.d = [np.load(Path(results) / m / f"run{r}.npz") for m in self.names]
        self.tow = np.asarray(self.d[0]["tow"], float)
        for m, d in zip(self.names, self.d):
            if len(d["tow"]) != len(self.tow) or not np.allclose(d["tow"], self.tow):
                raise ValueError(f"{m}: epochs differ from {self.names[0]}")
        self.C = np.stack([np.asarray(d[c], float) for d in self.d for c in CANDS], axis=1)
        self.fix = np.stack([np.asarray(d["rule_fix"], bool) for d in self.d], axis=1)
        h = hashlib.md5((ct + str(r) + "|".join(self.names)).encode()).hexdigest()[:12]
        cf = Path(cache) / f"af_{ct}{r}_{h}.npy"
        if cf.exists():
            self.af = np.load(cf)
        else:
            by, pos0 = load_rows(Path(results) / fmt(rows_pat, ct, r) / f"run{r}_rows.npz")
            self.af = np.full((len(self.tow), self.C.shape[1]), MISSING)
            for i in range(len(self.tow)):
                self.af[i] = np.nan_to_num(
                    epoch_fit(by.get(i), pos0.get(i), list(self.C[i])), nan=MISSING)
            Path(cache).mkdir(parents=True, exist_ok=True)
            np.save(cf, self.af)
        self.fin = np.all(np.isfinite(self.C), axis=2)


def select_af(run, n=6, margin=0.1):
    C, fix, af = run.C, run.fix, run.af
    m = af.shape[1]
    csum = np.cumsum(np.vstack([np.zeros(m), af]), axis=0)
    out = C[:, 0].copy()
    for i in range(len(C)):
        avg = (csum[i + 1] - csum[max(0, i - n + 1)]) / n
        fs = np.where(fix[i])[0]
        if len(fs):
            idx = [NC * k for k in fs]
            out[i] = C[i, idx[int(np.argmax(avg[idx]))]]
            continue
        ok = run.fin[i]
        if not ok.any():
            continue
        j = int(np.argmax(np.where(ok, avg, -1e9)))
        if avg[j] - avg[0] > margin:
            out[i] = C[i, j]
    return out


def _wmean(a, n):
    c = np.cumsum(np.vstack([np.zeros((1,) + a.shape[1:]), a]), axis=0)
    lo = np.maximum(0, np.arange(len(a)) - n + 1)
    return (c[np.arange(len(a)) + 1] - c[lo]) / n


def features(run):
    """Causal per-candidate features, shape (epochs, candidates, features)."""
    n, m = run.af.shape
    k = m // NC
    fin = run.fin
    cols = []

    def add(v):
        cols.append(np.broadcast_to(v, (n, m)).astype(float))

    for w in (1, 3, 6, 12, 30):
        a = _wmean(run.af, w)
        add(a)
        add(a - np.max(np.where(fin, a, -1e9), axis=1, keepdims=True))
        add(a - a[:, :1])
    add((run.af <= -1e8).astype(float))
    typ = np.tile(np.arange(NC), k)
    for c in range(NC):
        add((typ == c)[None, :] * np.ones((n, 1)))
    mem = np.repeat(np.arange(k), NC)
    add((mem == 0)[None, :] * np.ones((n, 1)))
    add(run.fix[:, mem].astype(float))
    add(run.fix.sum(axis=1, keepdims=True) * np.ones((1, m)))
    for key in MEMBER_KEYS:
        v = np.stack([np.asarray(d[key], float) if key in d.files else np.full(n, np.nan)
                      for d in run.d], axis=1)
        add(v[:, mem])
    cn = np.where(fin[..., None], run.C, np.nan)
    dist = np.linalg.norm(cn[:, :, None, :] - cn[:, None, :, :], axis=3)
    for t in (0.1, 0.3, 0.6):
        add(np.nansum(dist < t, axis=2) - 1)
    add(np.linalg.norm(cn - np.nanmedian(cn, axis=1)[:, None], axis=2))
    add(np.linalg.norm(cn - cn[:, :1], axis=2))
    flt = cn[:, mem * NC + 1]
    add(np.linalg.norm(cn - flt, axis=2))
    add(np.linalg.norm(cn - cn[:, mem * NC], axis=2))
    fixpos = np.where(run.fix[:, :, None], cn[:, ::NC], np.nan)
    dfix = np.nanmin(np.linalg.norm(cn[:, :, None, :] - fixpos[:, None, :, :], axis=3), axis=2)
    add(np.where(np.isfinite(dfix), dfix, 99.0))
    step = np.linalg.norm(np.diff(cn, axis=0, prepend=cn[:1]), axis=2)
    fstep = np.linalg.norm(np.diff(flt, axis=0, prepend=flt[:1]), axis=2)
    add(step - fstep)
    anyfix = run.fix.any(axis=1)
    since = np.full(n, 1e3)
    last = -1e9
    for i in range(n):
        if anyfix[i]:
            last = run.tow[i]
        since[i] = min(run.tow[i] - last, 1e3)
    add(since[:, None])
    return np.stack(cols, axis=2)


def select_learned(runs, refs):
    """Leave-one-run-out: for each run, train on the others, pick the
    candidate with the highest predicted P(within 0.5 m)."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    feats, labels, weights = {}, {}, {}
    for key, run in runs.items():
        feats[key] = features(run)
        rt, x, w = refs[key]
        rkey = {float(t): i for i, t in enumerate(rt)}
        ri = np.array([rkey.get(round(float(t), 1), -1) for t in run.tow])
        has = ri >= 0
        xe = np.full((len(run.tow), 3), np.nan)
        xe[has] = x[ri[has]]
        err = np.linalg.norm(np.where(run.fin[..., None], run.C, np.nan) - xe[:, None], axis=2)
        labels[key] = err <= 0.5
        we = np.zeros(len(run.tow))
        we[has] = w[ri[has]]
        weights[key] = (has, we)
    out = {}
    for test in runs:
        xs, ys, ws = [], [], []
        for key in runs:
            if key == test:
                continue
            has, we = weights[key]
            y, fin = labels[key], runs[key].fin
            informative = has & (y & fin).any(axis=1) & ~(y | ~fin).all(axis=1)
            msk = has[:, None] & fin & informative[:, None]
            xs.append(feats[key][msk])
            ys.append(y[msk])
            ws.append(np.broadcast_to(we[:, None], msk.shape)[msk] + 1e-3)
        xs, ys, ws = np.concatenate(xs), np.concatenate(ys), np.concatenate(ws)
        if len(ys) > 300000:
            sub = np.random.default_rng(0).choice(len(ys), 300000, replace=False)
            xs, ys, ws = xs[sub], ys[sub], ws[sub]
        clf = HistGradientBoostingClassifier(
            max_iter=150, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=200,
            l2_regularization=1.0, early_stopping=False, random_state=0)
        clf.fit(xs, ys, sample_weight=ws)
        run = runs[test]
        n, m = run.fin.shape
        p = clf.predict_proba(feats[test].reshape(n * m, -1))[:, 1].reshape(n, m)
        j = np.argmax(np.where(run.fin, p, -1), axis=1)
        pos = run.C[np.arange(n), j]
        none = ~run.fin.any(axis=1)
        pos[none] = run.C[none, 0]
        out[test] = pos
    return out


def up_unit(x):
    lat = np.arctan2(x[2], np.hypot(x[0], x[1]))
    lon = np.arctan2(x[1], x[0])
    return np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-root", default="datasets/PPC-Dataset-data",
                    help="PPC-Dataset root holding tokyo/ and nagoya/ run folders")
    ap.add_argument("--results", default=str(HERE / "results"))
    ap.add_argument("--rows", default="dmp_s10_{ct}{r}",
                    help="result dir pattern of the RBPF_DUMP_ROWS=1 row dump")
    ap.add_argument("--cache", default=str(HERE / "results" / "rt_ensemble_cache"))
    ap.add_argument("--members", nargs="+", required=True,
                    help="result dir patterns ({ct} tk|ng, {CT} TK|NG, {r} run)")
    ap.add_argument("--select", choices=("af", "learned"), default="af")
    ap.add_argument("--shift", action="store_true")
    ap.add_argument("--gaps", default="")
    ap.add_argument("--label-members", nargs="*", default=[])
    ap.add_argument("--label-votes", type=int, default=2)
    ap.add_argument("--save", default="", help="write <dir>/<CT>run<r>.npz outputs")
    args = ap.parse_args()

    runs = {k: Run(args.results, args.rows, args.cache, *k, args.members) for k in RUNS}
    refs = {k: reference(args.data_root, *k) for k in RUNS}
    if args.select == "af":
        pos = {k: select_af(run) for k, run in runs.items()}
    else:
        pos = select_learned(runs, refs)

    gaps = {}
    for k in RUNS:
        gaps[k] = None
        if args.gaps:
            f = Path(args.results) / fmt(args.gaps, *k) / f"run{k[1]}.npz"
            if f.exists():
                z = np.load(f)
                gaps[k] = z["gap_rows"] if "gap_rows" in z.files else None

    grid = np.round(np.arange(0.0, 0.31, 0.05), 2)
    shifts = {k: 0.0 for k in RUNS}
    if args.shift:
        s = np.zeros((len(RUNS), len(grid)))
        for i, k in enumerate(RUNS):
            anyfix = runs[k].fix.any(axis=1)
            up = up_unit(np.nanmedian(pos[k], axis=0))
            for j, dl in enumerate(grid):
                o = pos[k].copy()
                o[~anyfix] -= dl * up
                s[i, j] = official(refs[k], *with_gaps(runs[k].tow, o, gaps[k]))
        for i, k in enumerate(RUNS):
            others = [j for j in range(len(RUNS)) if j != i]
            shifts[k] = float(grid[int(np.argmax(s[others].mean(axis=0)))])

    rows = []
    for k in RUNS:
        run = runs[k]
        anyfix = run.fix.any(axis=1)
        o = pos[k].copy()
        o[~anyfix] -= shifts[k] * up_unit(np.nanmedian(o, axis=0))
        tow, p = with_gaps(run.tow, o, gaps[k])
        ok, w = per_epoch_ok(refs[k], tow, p)
        half = np.arange(len(w)) < len(w) // 2
        full = 100 * w[ok].sum() / w.sum()
        h1 = 100 * w[ok & half].sum() / w[half].sum()
        h2 = 100 * w[ok & ~half].sum() / w[~half].sum()
        lab_c = lab_w = np.nan
        if args.label_members:
            votes = np.zeros(len(run.tow), int)
            for m in args.label_members:
                d = np.load(Path(args.results) / fmt(m, *k) / f"run{k[1]}.npz")
                near = np.linalg.norm(np.asarray(d["sol_xyz"], float) - o, axis=1) < 0.2
                votes += np.asarray(d["rule_fix"], bool) & near
            lab = votes >= args.label_votes
            # score_label.py's definition: correct FIX over every rover epoch,
            # wrong FIX over every FIX
            e, n_rover = errors({"tow": run.tow, "sol_xyz": o}, k[1],
                                Path(args.data_root), CITY[k[0]])
            lab_c = 100 * np.sum(lab & (e <= 0.5)) / n_rover
            lab_w = 100 * np.sum(lab & (e > 0.5)) / max(int(lab.sum()), 1)
        rows.append((full, h1, h2, shifts[k], lab_c, lab_w))
        if args.save:
            Path(args.save).mkdir(parents=True, exist_ok=True)
            np.savez_compressed(Path(args.save) / f"{k[0].upper()}run{k[1]}.npz",
                                tow=tow, pos=p, shift=shifts[k])
    a = np.array(rows)
    names = "  ".join(f"{c.upper()}{r}" for c, r in RUNS)
    print(f"select={args.select} shift={args.shift} gaps={bool(args.gaps)} "
          f"members={len(args.members)}")
    print(f"{'':14s}{names}")
    for lab, j in (("OFFICIAL", 0), ("time half 1", 1), ("time half 2", 2)):
        print(f"{lab:12s} " + " ".join(f"{v:5.1f}" for v in a[:, j])
              + f" | mean {a[:, j].mean():5.2f}")
    if args.shift:
        print("height shift " + " ".join(f"{v:5.2f}" for v in a[:, 3]))
    if args.label_members:
        print("FIX correct  " + " ".join(f"{v:5.1f}" for v in a[:, 4]))
        print("wrong / FIX  " + " ".join(f"{v:5.2f}" for v in a[:, 5]))


if __name__ == "__main__":
    main()
