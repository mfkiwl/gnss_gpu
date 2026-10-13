"""WP50: integer carry and hysteresis for the RB-FGO-PF FIX label.

The PF decides integer sets; carrier continuity propagates them. A wrong
basin is a coherent position offset that carrier phase alone cannot reveal
while the geometry is static, so the label stays strict on entry, and an
integer set it has accepted is then carried through epochs where the PF's
agreement dips, for as long as the raw DD carrier keeps agreeing with it.

Rows use the RB-FGO-PF row-dump layout (`RbpfFgo._dump_rows`, 15 columns):
    [sys, f, ref, sat, el_ref, el_j, lam, Jx, Jy, Jz, b0, ddpr, held, mass,
     in_pairs]
b0 is the raw DD carrier minus the DD range at the epoch's float position
pos0, in cycles: b0 - J.(x - pos0)/lam = N + noise at the true position x.

- `HysteresisLabel`: enter FIX after the entry rule held for `entry`
  consecutive epochs, stay while the continuation rule holds, bridging up to
  `bridge` failed epochs (those are not labelled).
- `IntegerCarry`: single-difference integers per (sys, f) taken from a FIX
  epoch's position, re-checked each epoch by a carrier-only LS: pairs off by
  more than `res_cyc` cycles are dropped (slips), newly tracked satellites
  are adopted only when their fraction is within `ext_cyc`, and the carry
  stops below `kmin` pairs, on a data gap, on weak geometry, or when the DD
  pseudoranges disagree by more than `pr_med_m` (median).
- `backward_carry`: the same carry run backward in time from FIX epochs,
  for a fixed-lag (`max_s`) or post-processed output.
"""

from __future__ import annotations

import numpy as np

C_SYS, C_F, C_REF, C_SAT, C_LAM = 0, 1, 2, 3, 6
C_J = slice(7, 10)
C_B0, C_DDPR = 10, 11


class HysteresisLabel:
    def __init__(self, entry=5, bridge=2, gap_s=1.0):
        self.entry = int(entry)
        self.bridge = int(bridge)
        self.gap_s = float(gap_s)
        self.run = 0
        self.fixed = False
        self.miss = 0
        self.t_prev = None

    def update(self, tow, entry_ok, cont_ok):
        if self.t_prev is not None and abs(tow - self.t_prev) > self.gap_s:
            self.run = 0
            self.fixed = False
        self.t_prev = tow
        self.run = self.run + 1 if entry_ok else 0
        out = False
        if self.fixed:
            if cont_ok:
                out = True
                self.miss = 0
            else:
                self.miss += 1
                if self.miss > self.bridge:
                    self.fixed = False
                    self.miss = 0
        if not self.fixed and self.run >= self.entry:
            self.fixed = True
            self.miss = 0
            out = True
        return out


class IntegerCarry:
    def __init__(self, kmin=8, res_cyc=0.15, ext_cyc=0.10, rms_max_m=0.03,
                 pr_med_m=3.0, gap_s=1.0, max_cond=1e6):
        self.kmin = int(kmin)
        self.res_cyc = float(res_cyc)
        self.ext_cyc = float(ext_cyc)
        self.rms_max_m = float(rms_max_m)
        self.pr_med_m = float(pr_med_m)
        self.gap_s = float(gap_s)
        self.max_cond = float(max_cond)
        self.state = None
        self.t_prev = None
        self.t_src = None

    def reset(self):
        self.state = None

    def init(self, rows, pos0, x, tow):
        """Take the integer set at a FIX epoch (position x)."""
        self.t_prev = tow
        self.t_src = tow
        st = {}
        dx = np.asarray(x, dtype=float) - np.asarray(pos0, dtype=float)
        for r in rows:
            g = (int(r[C_SYS]), int(r[C_F]))
            ref, j = int(r[C_REF]), int(r[C_SAT])
            v = r[C_B0] - (r[C_J] @ dx) / r[C_LAM]
            n = np.rint(v)
            if abs(v - n) > self.res_cyc:
                continue
            m = st.setdefault(g, {ref: 0.0})
            if ref not in m:
                continue
            m[j] = m[ref] - n
        self.state = {g: m for g, m in st.items() if len(m) >= 2} or None

    def step(self, rows, pos0, tow, max_s=None):
        """Carried position at this epoch, or None (the carry then stops)."""
        if self.t_prev is not None and abs(tow - self.t_prev) > self.gap_s:
            self.state = None
        self.t_prev = tow
        if self.state is None or rows is None or len(rows) == 0:
            self.state = None
            return None
        if max_s is not None and self.t_src is not None and abs(tow - self.t_src) > max_s:
            self.state = None
            return None
        got = self._solve(rows, np.asarray(pos0, dtype=float))
        if got is None:
            self.state = None
            return None
        x, self.state = got
        return x

    def _solve(self, R, p0):
        st = self.state
        use = []
        for k, r in enumerate(R):
            m = st.get((int(r[C_SYS]), int(r[C_F])))
            ref, j = int(r[C_REF]), int(r[C_SAT])
            if m is not None and ref in m and j in m:
                use.append((k, m[ref] - m[j]))
        if len(use) < self.kmin:
            return None
        k_ = np.array([u[0] for u in use])
        z = np.array([u[1] for u in use])
        J = R[k_, C_J]
        lam = R[k_, C_LAM]
        y = lam * (R[k_, C_B0] - z)          # = J dx [m]
        keep = np.ones(len(y), dtype=bool)
        dx = None
        for _ in range(4):
            if keep.sum() < self.kmin:
                return None
            dx, *_ = np.linalg.lstsq(J[keep], y[keep], rcond=None)
            bad = np.abs((y - J @ dx) / lam) > self.res_cyc
            if np.array_equal(bad, ~keep):
                break
            keep = ~bad
        if keep.sum() < self.kmin:
            return None
        res_m = (y - J @ dx)[keep]
        if np.sqrt(np.mean(res_m ** 2)) > self.rms_max_m:
            return None
        try:
            if np.linalg.cond(J[keep].T @ J[keep]) > self.max_cond:
                return None
        except np.linalg.LinAlgError:
            return None
        if np.median(np.abs(R[:, C_DDPR] - R[:, C_J] @ dx)) > self.pr_med_m:
            return None
        new = {g: dict(m) for g, m in st.items()}
        for (k, _), kp in zip(use, keep):
            if not kp:
                new[(int(R[k, C_SYS]), int(R[k, C_F]))].pop(int(R[k, C_SAT]), None)
        for r in R:
            m = new.get((int(r[C_SYS]), int(r[C_F])))
            ref, j = int(r[C_REF]), int(r[C_SAT])
            if m is None or ref not in m or j in m:
                continue
            v = r[C_B0] - (r[C_J] @ dx) / r[C_LAM]
            n = np.rint(v)
            if abs(v - n) <= self.ext_cyc:
                m[j] = m[ref] - n
        new = {g: m for g, m in new.items() if len(m) >= 2}
        return p0 + dx, (new or None)


def backward_carry(rows_by_epoch, pos0_by_epoch, tow, fix, xyz, max_s=15.0,
                   **kw):
    """Carry integer sets backward in time from FIX epochs (fixed-lag
    `max_s` seconds). Returns (carried mask, carried positions); epochs
    already FIX are left to the forward output."""
    n = len(tow)
    out = np.zeros(n, dtype=bool)
    pos = np.full((n, 3), np.nan)
    car = IntegerCarry(**kw)
    for i in range(n - 1, -1, -1):
        R = rows_by_epoch.get(i)
        p0 = pos0_by_epoch.get(i)
        if R is None or p0 is None:
            car.reset()
            car.t_prev = tow[i]
            continue
        if fix[i] and np.all(np.isfinite(xyz[i])):
            car.init(R, p0, xyz[i], tow[i])
            continue
        x = car.step(R, p0, tow[i], max_s=max_s)
        if x is not None:
            out[i] = True
            pos[i] = x
    return out, pos
