"""WP60: CPU line-of-sight ray tracer for PLATEAU building meshes (no CUDA).

A uniform 2D grid over local ENU holds per-cell triangle lists and per-cell
top heights; each receiver-to-satellite ray walks the grid with a 2D DDA
(Moller-Trumbore tests; a cell is skipped once the ray is above its tallest
triangle). `GridTracer(triangles_ecef)` takes the [N, 3, 3] ECEF triangles of
`gnss_gpu.io.plateau.load_plateau` (or its npz cache) and offers the
`check_los` / `check_los_batch` interface of `gnss_gpu.bvh.BVHAccelerator`,
plus `inside_mask` for receivers that look enclosed by a building."""
import math

import numpy as np
from numba import njit, prange


def _enu_rot(x):
    lat = math.atan2(x[2], math.hypot(x[0], x[1])); lon = math.atan2(x[1], x[0])
    sl, cl, so, co = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    return np.array([[-so, co, 0.0], [-sl * co, -sl * so, cl], [cl * co, cl * so, sl]])


@njit(cache=True)
def _hit(o, d, v0, e1, e2):
    px = d[1] * e2[2] - d[2] * e2[1]
    py = d[2] * e2[0] - d[0] * e2[2]
    pz = d[0] * e2[1] - d[1] * e2[0]
    det = e1[0] * px + e1[1] * py + e1[2] * pz
    if abs(det) < 1e-12:
        return False
    inv = 1.0 / det
    tx = o[0] - v0[0]; ty = o[1] - v0[1]; tz = o[2] - v0[2]
    u = (tx * px + ty * py + tz * pz) * inv
    if u < 0.0 or u > 1.0:
        return False
    qx = ty * e1[2] - tz * e1[1]
    qy = tz * e1[0] - tx * e1[2]
    qz = tx * e1[1] - ty * e1[0]
    v = (d[0] * qx + d[1] * qy + d[2] * qz) * inv
    if v < 0.0 or u + v > 1.0:
        return False
    t = (e2[0] * qx + e2[1] * qy + e2[2] * qz) * inv
    return t > 1e-3


@njit(cache=True)
def _occluded(o, d, V0, E1, E2, cstart, ctris, czmax, xmin, ymin, cell, nx, ny, zall):
    if d[2] <= 0.0:
        return False
    if o[2] >= zall:
        return False
    t_end = (zall - o[2]) / d[2]
    inf = 1e30
    # clip the ray to the grid's 2D box (the receiver may sit outside it)
    t0 = 0.0; t1 = t_end
    bx0 = xmin; bx1 = xmin + nx * cell; by0 = ymin; by1 = ymin + ny * cell
    if d[0] != 0.0:
        ta = (bx0 - o[0]) / d[0]; tb = (bx1 - o[0]) / d[0]
        t0 = max(t0, min(ta, tb)); t1 = min(t1, max(ta, tb))
    elif o[0] < bx0 or o[0] >= bx1:
        return False
    if d[1] != 0.0:
        ta = (by0 - o[1]) / d[1]; tb = (by1 - o[1]) / d[1]
        t0 = max(t0, min(ta, tb)); t1 = min(t1, max(ta, tb))
    elif o[1] < by0 or o[1] >= by1:
        return False
    if t0 > t1:
        return False
    px = o[0] + d[0] * t0; py = o[1] + d[1] * t0
    cx = min(max(int(math.floor((px - xmin) / cell)), 0), nx - 1)  # noqa: RUF046 (numba)
    cy = min(max(int(math.floor((py - ymin) / cell)), 0), ny - 1)  # noqa: RUF046 (numba)
    if d[0] > 0:
        sx = 1; tmx = (xmin + (cx + 1) * cell - o[0]) / d[0]; tdx = cell / d[0]
    elif d[0] < 0:
        sx = -1; tmx = (xmin + cx * cell - o[0]) / d[0]; tdx = -cell / d[0]
    else:
        sx = 0; tmx = inf; tdx = inf
    if d[1] > 0:
        sy = 1; tmy = (ymin + (cy + 1) * cell - o[1]) / d[1]; tdy = cell / d[1]
    elif d[1] < 0:
        sy = -1; tmy = (ymin + cy * cell - o[1]) / d[1]; tdy = -cell / d[1]
    else:
        sy = 0; tmy = inf; tdy = inf
    t_in = t0
    while True:
        c = cy * nx + cx
        if o[2] + d[2] * t_in <= czmax[c]:
            for k in range(cstart[c], cstart[c + 1]):
                j = ctris[k]
                if _hit(o, d, V0[j], E1[j], E2[j]):
                    return True
        if tmx < tmy:
            t_in = tmx; tmx += tdx; cx += sx
        else:
            t_in = tmy; tmy += tdy; cy += sy
        if t_in > t_end or cx < 0 or cy < 0 or cx >= nx or cy >= ny:
            return False


@njit(parallel=True, cache=True)
def _los_batch(O, D, valid, V0, E1, E2, cstart, ctris, czmax, xmin, ymin, cell, nx, ny, zall):
    B, S = valid.shape
    out = np.ones((B, S), dtype=np.bool_)
    for b in prange(B):
        for s in range(S):
            if valid[b, s]:
                out[b, s] = not _occluded(O[b], D[b, s], V0, E1, E2, cstart, ctris, czmax,
                                          xmin, ymin, cell, nx, ny, zall)
    return out


class GridTracer:
    def __init__(self, triangles, cell=8.0):
        tri = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        self.o = tri.reshape(-1, 3).mean(axis=0)
        self.R = _enu_rot(self.o)
        T = (tri - self.o) @ self.R.T
        self.V0 = np.ascontiguousarray(T[:, 0]); self.E1 = np.ascontiguousarray(T[:, 1] - T[:, 0])
        self.E2 = np.ascontiguousarray(T[:, 2] - T[:, 0])
        self.cell = float(cell)
        self.xmin = float(T[..., 0].min()) - cell; self.ymin = float(T[..., 1].min()) - cell
        self.nx = math.ceil((T[..., 0].max() - self.xmin) / cell) + 2
        self.ny = math.ceil((T[..., 1].max() - self.ymin) / cell) + 2
        ix0 = np.floor((T[:, :, 0].min(1) - self.xmin) / cell).astype(np.int64)
        ix1 = np.floor((T[:, :, 0].max(1) - self.xmin) / cell).astype(np.int64)
        iy0 = np.floor((T[:, :, 1].min(1) - self.ymin) / cell).astype(np.int64)
        iy1 = np.floor((T[:, :, 1].max(1) - self.ymin) / cell).astype(np.int64)
        zt = T[:, :, 2].max(1)
        cells, tris = [], []
        for j in range(len(T)):
            xs = np.arange(ix0[j], ix1[j] + 1); ys = np.arange(iy0[j], iy1[j] + 1)
            cc = (ys[:, None] * self.nx + xs[None, :]).ravel()
            cells.append(cc); tris.append(np.full(len(cc), j))
        cells = np.concatenate(cells); tris = np.concatenate(tris)
        order = np.argsort(cells, kind='stable')
        cells = cells[order]; tris = tris[order]
        ncell = self.nx * self.ny
        cnt = np.bincount(cells, minlength=ncell)
        self.cstart = np.zeros(ncell + 1, dtype=np.int64); self.cstart[1:] = np.cumsum(cnt)
        self.ctris = tris.astype(np.int64)
        self.czmax = np.full(ncell, -1e30)
        np.maximum.at(self.czmax, cells, zt[tris])
        self.zall = float(T[..., 2].max())
        self.n_triangles = len(T); self.n_nodes = ncell

    @classmethod
    def from_building_model(cls, model):
        return cls(model.triangles)

    def check_los_batch(self, rx, sat):
        rx = np.asarray(rx, dtype=np.float64).reshape(-1, 3)
        sat = np.asarray(sat, dtype=np.float64).reshape(len(rx), -1, 3)
        valid = np.all(np.isfinite(sat), axis=2)
        d = np.where(valid[..., None], sat - rx[:, None, :], 0.0)
        n = np.linalg.norm(d, axis=2, keepdims=True)
        d = np.where(n > 0, d / np.where(n > 0, n, 1.0), 0.0)
        O = np.ascontiguousarray((rx - self.o) @ self.R.T)
        D = np.ascontiguousarray(d @ self.R.T)
        return _los_batch(O, D, valid, self.V0, self.E1, self.E2, self.cstart, self.ctris,
                          self.czmax, self.xmin, self.ymin, self.cell, self.nx, self.ny, self.zall)

    def inside_mask(self, rx):
        """True where the receiver looks enclosed (zenith and eight 35-deg
        rays all blocked): a position error that put it inside a building,
        where every satellite would come out NLOS."""
        rx = np.asarray(rx, dtype=np.float64).reshape(-1, 3)
        dirs = [[0.0, 0.0, 1.0]]
        for k in range(8):
            a = 2 * math.pi * k / 8
            dirs.append([math.sin(a) * math.cos(math.radians(35)), math.cos(a) * math.cos(math.radians(35)),
                         math.sin(math.radians(35))])
        Dn = np.ascontiguousarray(np.broadcast_to(np.array(dirs), (len(rx), 9, 3)))
        O = np.ascontiguousarray((rx - self.o) @ self.R.T)
        los = _los_batch(O, Dn, np.ones((len(rx), 9), dtype=np.bool_), self.V0, self.E1, self.E2,
                         self.cstart, self.ctris, self.czmax, self.xmin, self.ymin, self.cell,
                         self.nx, self.ny, self.zall)
        return ~los.any(axis=1)

    def check_los(self, rx, sat):
        sat = np.asarray(sat, dtype=np.float64).reshape(-1, 3)
        return self.check_los_batch(np.asarray(rx, dtype=np.float64)[None], sat[None])[0]


if __name__ == '__main__':
    # self-test: a 20 m tall wall 10 m east of the receiver blocks a satellite low in the east
    wall = np.array([[[10, -50, 0], [10, 50, 0], [10, 50, 20]], [[10, -50, 0], [10, 50, 20], [10, -50, 20]]], float)
    o = np.array([-3.9e6, 3.4e6, 3.6e6]); R = _enu_rot(o)
    tri = wall @ R + o
    g = GridTracer(tri, cell=4.0)
    rx = o + np.array([0, 0, 1.5]) @ R
    def sat_at(az, el):
        a, e = math.radians(az), math.radians(el)
        return rx + 2e7 * (np.array([math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)]) @ R)
    s = np.array([sat_at(90, 30), sat_at(90, 80), sat_at(270, 30), sat_at(0, 10)])
    print(g.check_los(rx, s), 'expected [False  True  True  True]')
