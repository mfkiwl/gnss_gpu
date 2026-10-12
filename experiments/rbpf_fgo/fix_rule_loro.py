#!/usr/bin/env python3
"""Leave-one-run-out selection of a causal FIX-label rule (WP44).

    python fix_rule_loro.py --data-root .../PPC-Dataset ne_s10 ne_s11 ne_s12

Runs must carry the WP44 diagnostics (`rb_nb_at`, `rb_nb_at_ne`,
`rb_ne_mass`, `base_smode`, `base_nb`). Positions are never changed; a rule
only decides which epochs are labelled FIX:

    FIX  <=>  nb_at[t] >= F                       PF: MAP pairs above agreement t
          or  (nb_at_ne[t2] >= F2 and ne_mass >= M)
                                                  PF: integer hypotheses agree
                                                  while the flat one holds mass
          or  base_smode == 4 and base_nb >= B     shared filter's own LAMBDA
                                                  fix (phase 1 fix-and-hold,
                                                  phase 2 ratio test)
          or  afv_frac >= A and afv_n >= AN       (--afv) ambiguity function
                                                  at the reported position:
                                                  share of the epoch's DD
                                                  carrier rows within 0.15
                                                  cycle of an integer
          or  witness FIX and |pos - witness pos| < D
                                                  an independent real-time
                                                  RTK engine (--witness-pos,
                                                  e.g. libgnss++ KF-RTK) has
                                                  a FIX that agrees with the
                                                  PF's own reported position

Every label also passes the PF's own FIX-output guards (reported position
within 5 m of the code-only DDPR position, and the float / DDPR / fix vote).

For each held-out run, the grid point that maximises correct FIX on the
other two runs (all seeds pooled) while keeping every training run's wrong
FIX share <= --cap (default 0.7%, a margin under the 1% target) is applied
to the held-out run. With --fixed t,F,t2,F2,M,B the given rule is scored on
every run instead (no selection).
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from fix_rules import CITY, errors
from fix_witness import afv_features
from rbpf_fgo import NB_AT_THRESH

GRID = list(itertools.product(
    (3, 4, 5),            # t index  (0.95, 0.98, 0.99)
    (5, 9, 12, 15),       # F
    (3, 5),               # t2 index (0.95, 0.99)
    (12, 15, 99),         # F2 (99 = off)
    (0.1, 0.3),           # M
    (5, 9, 99),           # B (99 = off)
))
WIT_D = (0.0, 0.05, 0.1, 0.2)  # witness agreement radius (0 = off)
AFV = ((2.0, 0), (0.95, 8), (0.95, 10), (0.95, 12), (0.9, 12),
       (0.9, 16))                # (A, AN); A > 1 = off
# --guards: extra vetoes on every label (WP47): code-only DDPR quality at
# most QMAX, at least NPMIN PF pairs, non-empty mass at least NMMIN
GUARDS = list(itertools.product((99.0, 1.0, 0.8, 0.6), (0, 16, 20, 24),
                                (0.0, 0.9)))


GUARD_M = float(__import__("os").environ.get("FIX_RULE_GUARD_M", "5.0"))


def load_witness(path, d):
    """RTKLIB/libgnss++ .pos (GPS week/TOW, ECEF, status 4 = FIX) aligned
    to the npz rows by TOW; returns (fix mask, ECEF)."""
    with open(path) as fh:
        A = np.array([[float(x) for x in ln.split()[:9]] for ln in fh
                      if ln.strip() and not ln.startswith("%")])
    at = {round(float(t), 1): k for k, t in enumerate(A[:, 1])}
    tow = np.asarray(d["tow"], dtype=float)
    fx = np.zeros(len(tow), dtype=bool)
    xyz = np.full((len(tow), 3), np.nan)
    for i, t in enumerate(tow):
        k = at.get(round(float(t), 1))
        if k is not None:
            fx[i] = A[k, 8] == 4
            xyz[i] = A[k, 2:5]
    return fx, xyz


def prepare(d, run, data_root, witness=None, afv=None, city=None):
    e, n = errors(d, run, data_root, city)
    ok = np.isfinite(e) & (np.asarray(d["smode"]) != 6)
    # the PF's own FIX-output guards (RBPF_FIX_MAX_DPR = 5 m and the
    # float / DDPR / fix vote 6 m / 3.5 m), applied to the reported
    # position against this epoch's code-only DDPR position
    sol = np.asarray(d["sol_xyz"], dtype=float)
    ddpr = np.asarray(d["ddpr_xyz"], dtype=float)
    flt = np.asarray(d["flt_xyz"], dtype=float)
    d_fix = np.linalg.norm(sol - ddpr, axis=1)
    d_flt = np.linalg.norm(flt - ddpr, axis=1)
    has = np.isfinite(d_fix)
    guard = ~(has & ((d_fix > GUARD_M) | ((d_flt > 6.0) & (d_fix > 3.5))))
    if GUARD_M <= 0:
        guard = np.ones(len(e), dtype=bool)
    # distance from the reported position to an agreeing witness FIX
    # (inf where the witness has no FIX); DR-filled epochs never qualify
    wit_d = np.full(len(e), np.inf)
    if witness is not None:
        wfx, wxyz = witness
        dist = np.linalg.norm(sol - wxyz, axis=1)
        wit_d = np.where(wfx & np.isfinite(dist), dist, np.inf)
    return {"e": e, "n": n, "ok": ok & guard,
            "pf_ok": np.asarray(d["rb_ok"]) == 1,
            "nb_at": np.asarray(d["rb_nb_at"]),
            "nb_at_ne": np.asarray(d["rb_nb_at_ne"]),
            "ne_mass": np.asarray(d["rb_ne_mass"]),
            "base_fix": np.asarray(d["base_smode"]) == 4,
            "base_nb": np.asarray(d["base_nb"]),
            "shipped": (np.asarray(d["smode"]) == 4) & (np.asarray(d["rb_nb"]) >= 12),
            "wit_d": wit_d,
            "ddpr_qual": np.asarray(d.get("rb_ddpr_qual", np.full(len(e), np.nan)),
                                    dtype=float),
            "npairs": np.asarray(d["rb_npairs"]),
            "afv_n": afv[0] if afv is not None else np.zeros(len(e), int),
            "afv_frac": afv[1] if afv is not None else np.full(len(e), np.nan)}


def apply(p, t, f, t2, f2, m, b, wd=0.0, av=2.0, an=0,
          qmax=99.0, npmin=0, nmmin=0.0):
    fx = p["pf_ok"] & (p["nb_at"][:, t] >= f)
    fx |= p["pf_ok"] & (p["nb_at_ne"][:, t2] >= f2) & (p["ne_mass"] >= m)
    fx |= p["base_fix"] & (p["base_nb"] >= b)
    if wd > 0:
        fx |= p["wit_d"] < wd
    if av <= 1.0:
        with np.errstate(invalid="ignore"):
            fx |= p["pf_ok"] & (p["afv_frac"] >= av) & (p["afv_n"] >= an)
    if qmax < 99:
        with np.errstate(invalid="ignore"):
            fx &= ~(p["ddpr_qual"] > qmax)
    if npmin > 0:
        fx &= p["npairs"] >= npmin
    if nmmin > 0:
        with np.errstate(invalid="ignore"):
            fx &= p["ne_mass"] >= nmmin
    return fx & p["ok"]


def score(p, fx):
    good = fx & (p["e"] < 0.5)
    return int(good.sum()), int(fx.sum() - good.sum()), p["n"]


def describe(g):
    t, f, t2, f2, m, b = g[:6]
    wd = g[6] if len(g) > 6 else 0.0
    av, an = (g[7], g[8]) if len(g) > 8 else (2.0, 0)
    qmax, npmin, nmmin = g[9:12] if len(g) > 11 else (99.0, 0, 0.0)
    s = f"nb_at[{NB_AT_THRESH[t]}]>={f}"
    if f2 < 99:
        s += f" | nb_at_ne[{NB_AT_THRESH[t2]}]>={f2}&ne_mass>={m}"
    if b < 99:
        s += f" | base_nb>={b}"
    if wd > 0:
        s += f" | witness FIX within {wd} m"
    if av <= 1.0:
        s += f" | afv_frac>={av}&n>={an}"
    if qmax < 99:
        s += f"; veto ddpr_qual>{qmax}"
    if npmin > 0:
        s += f"; need npairs>={npmin}"
    if nmmin > 0:
        s += f"; need ne_mass>={nmmin}"
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="*",
                    help="result prefixes P scored on P_r{1,2,3} of PPC_CITY")
    ap.add_argument("--set", action="append", default=[],
                    help="city:run:prefix (repeatable): score results/"
                         "<prefix>_r<run> on that city's run; with --set the "
                         "folds are every (city, run) given")
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--results", type=Path, default=_HERE / "results")
    ap.add_argument("--cap", type=float, default=0.7)
    ap.add_argument("--fixed", default=None,
                    help="t_idx,F,t2_idx,F2,M,B[,D]: score this rule on every run")
    ap.add_argument("--afv", action="store_true",
                    help="add the ambiguity-function term (needs RBPF_AFV=1 "
                         "runs, or runN_rows.npz from RBPF_DUMP_ROWS=1)")
    ap.add_argument("--guards", action="store_true",
                    help="add the WP47 veto grid (DDPR quality, PF pair "
                         "count, non-empty mass)")
    ap.add_argument("--witness-pos", default=None,
                    help="independent RTK .pos per run, '{run}' is replaced "
                         "(adds the witness-agreement radius D to the grid)")
    a = ap.parse_args()
    sources = [(CITY, r, pre) for pre in a.prefixes for r in (1, 2, 3)]
    sources += [(c, int(r), pre) for c, r, pre in
                (x.split(":") for x in a.set)]
    runs = {}
    for city, r, pre in sources:
        key = f"{city}/run{r}" if a.set else r
        lst = runs.setdefault(key, [])
        path = a.results / f"{pre}_r{r}" / f"run{r}.npz"
        if path.exists():
            d = dict(np.load(path))
            wit = (load_witness(a.witness_pos.format(run=r), d)
                   if a.witness_pos else None)
            afv = None
            if a.afv:   # in-run RBPF_AFV=1 columns, else the row dump
                afv = ((d["rb_afv_n"], d["rb_afv_frac"])
                       if "rb_afv_n" in d and np.any(d["rb_afv_n"] > 0)
                       else afv_features(path))
            lst.append(prepare(d, r, a.data_root, wit, afv, city))

    def report(r, g, tag):
        rows = []
        for p in runs[r]:
            c, w, n = score(p, apply(p, *g))
            c0, w0, _ = score(p, p["shipped"] & p["ok"])
            rows.append((100 * c / n, 100 * w / max(c + w, 1),
                         100 * c0 / n, 100 * w0 / max(c0 + w0, 1)))
        v = np.asarray(rows)
        mean = v.mean(axis=0)
        sd = v.std(axis=0, ddof=1) if len(v) > 1 else np.zeros(4)
        per_seed = " ".join(f"{x[0]:.1f}/{x[1]:.2f}" for x in v)
        name = r if isinstance(r, str) else f"run{r}"
        print(f"{name} {tag}: correct {mean[0]:.1f}±{sd[0]:.1f}% "
              f"wrong/fix {mean[1]:.2f}% (seeds: {per_seed})  "
              f"| shipped {mean[2]:.1f}% / {mean[3]:.2f}%")

    if a.fixed:
        vals = a.fixed.split(",")
        g = (int(vals[0]), int(vals[1]), int(vals[2]), int(vals[3]),
             float(vals[4]), int(vals[5]),
             float(vals[6]) if len(vals) > 6 else 0.0,
             float(vals[7]) if len(vals) > 7 else 2.0,
             int(vals[8]) if len(vals) > 8 else 0,
             float(vals[9]) if len(vals) > 9 else 99.0,
             int(vals[10]) if len(vals) > 10 else 0,
             float(vals[11]) if len(vals) > 11 else 0.0)
        print("rule:", describe(g))
        for r, lst in runs.items():
            if lst:
                report(r, g, "fixed rule")
        return
    for held in list(runs):
        train = [r for r in runs if r != held and runs[r]]
        if not runs[held] or not train:
            continue
        best = None
        wds = WIT_D if a.witness_pos else (0.0,)
        afvs = AFV if a.afv else ((2.0, 0),)
        gds = GUARDS if a.guards else ((99.0, 0, 0.0),)
        grid = [g + (wd,) + av + gd for g in GRID for wd in wds
                for av in afvs for gd in gds]
        for g in grid:
            tot = 0.0
            feasible = True
            for r in train:
                gs = ws = 0
                for p in runs[r]:
                    c, w, _ = score(p, apply(p, *g))
                    gs += c
                    ws += w
                if gs + ws and 100.0 * ws / (gs + ws) > a.cap:
                    feasible = False
                    break
                tot += gs / sum(p["n"] for p in runs[r])
            if feasible and (best is None or tot > best[0]):
                best = (tot, g)
        if best is None:
            print(f"held-out {held}, trained on {train}: no rule meets the cap")
            continue
        print(f"held-out {held}, trained on {train}: {describe(best[1])}")
        report(held, best[1], "held-out")


if __name__ == "__main__":
    main()
