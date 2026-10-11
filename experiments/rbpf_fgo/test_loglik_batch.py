#!/usr/bin/env python3
"""Parity check: the batched WP43 likelihood equals the scalar `_loglik`.

    .venv/bin/python test_loglik_batch.py

Builds random synthetic epoch problems (two constellation groups, PR and
CP rows, some pairs without carrier) and scores random partial basins
with random prior centres/covariances both ways.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
for p in (str(_HERE / "tc" / "src"), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from rbpf_fgo import EpochProblem, RbpfConfig, RbpfFgo


def make_problem(rng):
    prob = EpochProblem()
    groups = [((0, 0), 1, list(range(2, 9))), ((2, 1), 40, list(range(41, 46)))]
    pairs = []
    for g, ref, mem in groups:
        for j in mem:
            pairs.append((g, ref, j))
    rows = {"J": [], "v0": [], "sig": [], "iscp": [], "pair": [], "lam": [], "bmeas": []}
    for pi, _ in enumerate(pairs):
        J = rng.normal(size=3)
        J /= np.linalg.norm(J)
        rows["J"].append(J)
        rows["v0"].append(rng.normal(scale=2.0))
        rows["sig"].append(abs(rng.normal(0.8, 0.2)))
        rows["iscp"].append(False)
        rows["pair"].append(-1)
        rows["lam"].append(0.0)
        rows["bmeas"].append(0.0)
        if rng.random() < 0.85:
            rows["J"].append(J)
            rows["v0"].append(0.0)
            rows["sig"].append(0.008 + abs(rng.normal(0, 0.004)))
            rows["iscp"].append(True)
            rows["pair"].append(pi)
            rows["lam"].append(0.19 if pi % 2 else 0.24)
            rows["bmeas"].append(rng.normal(scale=20.0))
    prob.pairs = pairs
    nb = len(pairs)
    prob.y = rng.normal(scale=20.0, size=nb)
    L = rng.normal(size=(nb, nb)) * 0.1
    prob.Qb = L @ L.T + np.eye(nb) * 0.01
    prob.pos = rng.normal(size=3) * 1e6
    Pp = rng.normal(size=(3, 3)) * 0.3
    prob.Ppos = Pp @ Pp.T + np.eye(3)
    prob.rows_J = np.asarray(rows["J"])
    prob.rows_v0 = np.asarray(rows["v0"])
    prob.rows_sig = np.asarray(rows["sig"])
    prob.rows_iscp = np.asarray(rows["iscp"], dtype=bool)
    prob.rows_pair = np.asarray(rows["pair"], dtype=int)
    prob.rows_lam = np.asarray(rows["lam"])
    prob.rows_bmeas = np.asarray(rows["bmeas"])
    prob.ok = True
    return prob


def main():
    rng = np.random.default_rng(7)
    rb = RbpfFgo(RbpfConfig())
    worst = 0.0
    n = 0
    for _ in range(20):
        prob = make_problem(rng)
        hyps = []
        for _k in range(15):
            z = {pi: int(np.rint(prob.rows_bmeas[r] + rng.integers(-1, 2) * (rng.random() < 0.3)))
                 for r, pi in enumerate(prob.rows_pair)
                 if prob.rows_iscp[r] and rng.random() < 0.7}
            H = rb._adopt_map(prob, z) if z else {}
            centre = rng.normal(scale=0.5, size=3) if rng.random() < 0.6 else np.zeros(3)
            Pc = rng.normal(size=(3, 3)) * 0.05
            Pc = Pc @ Pc.T + np.eye(3) * 1e-3 if np.any(centre) else prob.Ppos
            hyps.append((H, centre, Pc))
        dds = np.array([rb._pair_dd(H, prob) if H else np.full(len(prob.pairs), np.nan)
                        for H, _, _ in hyps])
        ll_b, _, _ = rb._loglik_batch(prob, dds, np.array([h[1] for h in hyps]),
                                      np.array([h[2] for h in hyps]))
        for (H, centre, Pc), lb in zip(hyps, ll_b):
            if np.any(centre):
                state = {"pos": prob.pos + centre, "P": Pc}
                rb._cluster_state = lambda _H, s=state: s
            else:
                rb._cluster_state = lambda _H: None
            ls = rb._loglik(H, prob, {})
            worst = max(worst, abs(ls - lb) / max(1.0, abs(ls)))
            n += 1
    print(f"{n} hypotheses, worst relative |scalar - batch| = {worst:.2e}")
    assert worst < 1e-9, worst


if __name__ == "__main__":
    main()
