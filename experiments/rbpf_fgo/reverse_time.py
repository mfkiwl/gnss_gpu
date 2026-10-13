"""WP51: run RB-FGO-PF backward in time on a time-mirrored copy of a run.

The filter itself only runs forward. A backward pass feeds it a mirrored
world: epoch times tau = t_first + t_last - t (so the last rover epoch comes
first), and every measurement rewritten so that the mirrored trajectory
p(tau) = p_true(t) obeys the same physics:

- velocity and every rate negate: Doppler D -> -D, satellite velocity
  vs -> -vs, gyro -> -gyro. Specific force (d^2p/dtau^2 = d^2p/dt^2) and
  pseudorange / carrier phase are unchanged.
- satellite orbits and clocks are evaluated at the true transmission time
  t_rx - P/c (the light-time correction does not reverse).
- RINEX LLI marks a slip at the first epoch after it; in mirrored order that
  is the epoch before it in true time, so each satellite/band LLI value moves
  to that satellite's previous observation.
- each 100 Hz IMU sample covers [t - 0.01, t]; its mirrored stamp is the end
  of the same interval, M - t + 0.01.

In the mirrored world the vehicle drives backward (velocity opposite to the
body x axis), so the Phase-1 heading seed from displacement is turned by pi
(RBPF_REVERSE=1, read by gtsam_rtk_standalone). Outputs are mapped back to
true time by wp16_run_rbpf.py --reverse.
"""

from __future__ import annotations

import copy

import numpy as np


class Mirror:
    """t -> t_first + t_last - t, on cssrlib gtime_t and on GPS TOW."""

    def __init__(self, t_first, t_last, tow_first, tow_last):
        self.mi = int(t_first.time) + int(t_last.time)
        self.ms = float(t_first.sec) + float(t_last.sec)
        self.mtow = float(tow_first) + float(tow_last)

    def time(self, t):
        from cssrlib.gnss import gtime_t
        ti = self.mi - int(t.time)
        ts = round(self.ms - float(t.sec), 9)
        while ts < 0.0:
            ts += 1.0
            ti -= 1
        while ts >= 1.0:
            ts -= 1.0
            ti += 1
        return gtime_t(ti, ts)

    def tow(self, tow):
        return self.mtow - np.asarray(tow, dtype=float)


def _shift_lli(obs_list):
    """Move each (sat, band) LLI value to that satellite's previous
    observation (true time order in, true time order out)."""
    last = {}               # sat -> (epoch, row) of its previous observation
    new = [np.zeros_like(o.lli) for o in obs_list]
    for k, o in enumerate(obs_list):
        for i, sat in enumerate(o.sat):
            prev = last.get(int(sat))
            if prev is not None:
                kp, ip = prev
                nf = min(new[kp].shape[1], o.lli.shape[1])
                new[kp][ip, :nf] = o.lli[i, :nf]
            last[int(sat)] = (k, i)
    return new


def mirror_obs(obs_list, mirror):
    """Mirrored copy of a true-time-ordered observation list."""
    lli = _shift_lli(obs_list)
    out = []
    for o, ll in zip(reversed(obs_list), reversed(lli)):
        m = copy.copy(o)
        m.t = mirror.time(o.t)
        m.D = -np.asarray(o.D, dtype=float) if np.size(o.D) else o.D
        m.lli = ll
        out.append(m)
    return out


def read_all(dec):
    from cssrlib.rinex import _obs_is_eof
    out = []
    while True:
        o = dec.decode_obs()
        if _obs_is_eof(o):
            return out
        out.append(o)


class ListDecoder:
    """Stands in for a cssrlib rnxdec in sync_obs_hold."""

    def __init__(self, obs_list):
        self._it = iter(obs_list)

    def decode_obs(self):
        from cssrlib.gnss import Obs
        return next(self._it, None) or Obs()


def mirror_imu(imu_data, mirror, dt=0.01):
    out = []
    for im in reversed(imu_data):
        out.append({'tow': float(mirror.tow(im['tow'])) + dt,
                    'acc': im['acc'],
                    'gyro': -np.asarray(im['gyro'], dtype=float)})
    return out


def patch_satposs(mirror):
    """Satellite states at the true transmission time; velocity mirrored."""
    import cssrlib.ephemeris as ce
    import cssrlib.rtk as cr
    orig = ce.satposs

    def satposs(obs, nav, cs=None, orb=None):
        o = copy.copy(obs)
        o.t = mirror.time(obs.t)
        rs, vs, dts, svh, nsat = orig(o, nav, cs, orb)
        return rs, -vs, dts, svh, nsat

    cr.satposs = satposs
    return orig
