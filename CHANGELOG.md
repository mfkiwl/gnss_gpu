# Changelog

## Unreleased

- **RB-FGO-PF WP46** (`experiments/rbpf_fgo/`): validated on Nagoya. WP45's
  Tokyo-selected FIX label marked 8.5% / 16.2% of Nagoya 2 / 3 fixes wrong.
  `run_ship.sh` now ships a label rule selected leave-one-run-out over Tokyo
  1–3 and Nagoya 1–2 (`--fix-rule 3,15,3,15,0.1,99`). Correct FIX (3 seeds):
  Tokyo 56.1 / 76.4 / 75.8%, Nagoya 61.0 / 46.5 / 12.3%. Wrong FIX stays at
  or below 0.37% of fixes on every seed except Nagoya 3 (6.2%). Positions
  are unchanged. Also added: `PPC_CITY=nagoya` (lever arm and scorers),
  `fix_rule_loro.py --set city:run:prefix`, and the default-off shared-graph
  QC options `QC_BASE_MASK` and `QC_ANY_BAND`. `RBPF_SHIP=wp45` reproduces
  the previous label.
- **RB-FGO-PF WP45** (`experiments/rbpf_fgo/`): `run_ship.sh` now runs one
  configuration on all runs. The runner requests two bands per satellite.
  With three, cssrlib dropped every satellite without L5, and BeiDou used
  BDS-3-only B1C; WP45 uses BeiDou B1I + B3I and drops GLONASS. Basins
  survive graph re-seeds, and the `.pos` FIX flag is a leave-one-run-out
  selected rule on the PF's agreement mass (`--fix-rule`). Tokyo PPC correct
  FIX (3 seeds) goes from 35.4 / 64.1 / 68.5% to 66.6 / 83.9 / 84.3%, with
  0.56 / 0.09 / 0.04% of fixes wrong. PPC OFFICIAL goes from 63.7 / 85.1 /
  86.9% to 80.8 / 92.7 / 90.4%. `RBPF_SHIP=wp43` reproduces the previous
  release.
- **RB-FGO-PF WP43** (`experiments/rbpf_fgo/`): the shipped run now reports
  each epoch's MAP basin tracked position belief. Every particle lineage
  keeps its own IMU-predicted Gaussian position belief, updated under its
  own integers. On rover epochs the graph abandons, the run writes an IMU
  dead-reckoned position. FIX decisions are unchanged. Tokyo PPC
  `<50cm_full%` (3 seeds) goes from 60.1 / 78.9 / 81.9 to 63.1 / 82.7 / 86.7,
  and PPC OFFICIAL from 57.1 / 82.2 / 83.5 to 63.7 / 85.1 / 86.9.
  `RBPF_SHIP=wp18` reproduces the previous output.
- **Behavior change**: `ParticleFilterDevice(resampling="megopolis")`, the
  default, now runs the corrected Megopolis (Chesser et al. 2021) with
  `megopolis_iterations=60`. The previous kernel did not converge to the weight
  distribution and remains available as `"megopolis_legacy"` for reproducing
  old results. On PPC (6 routes) PF P50 improves by 0.7 m; on UrbanNav
  Odaiba the forward RMS improves by 0.6 m and the smoothed output is unchanged
  within seed noise.
- `ParticleFilterDevice(resampling="megopolis_coalesced")`: opt-in Megopolis
  (Chesser et al. 2021) with ancestor-weight acceptance and block-shared,
  coalesced proposals. It converges to the weight distribution as iterations
  grow, unlike the default `"megopolis"`, and runs 3-13x faster (9 ms at
  B=15, 18 ms at B=240 for 1M particles). New `megopolis_iterations` argument
  (default 15). `ParticleFilterDevice` now rejects unknown `resampling` values.
- `ParticleFilterDevice` Megopolis resampling now mixes int32 ancestor indices
  and gathers the 16-double particle state once instead of copying it on
  every iteration. Bit-identical output; 1M-particle `predict → update` goes
  from 153 ms to 32 ms on a Turing GPU.
- Fixed a GPU memory leak in the host-buffered `ParticleFilter` Megopolis
  resampling: 4 of 9 device buffers were never freed (32 MB per call at 1M
  particles).

## 0.3.0 - 2026-07-29

- Added immutable evaluation contracts and mandatory negative holdouts.
- Added truth-free evidence detection, DDPR profiles, and weighted arc screens.
- Added IMU/map multi-hypothesis outage recovery.
- Added persistent CUDA batching, adaptive particle budgets, and runtime audits.
- Added cross-domain validation and fail-closed adaptation guidance.
- Added a ROS 2 lifecycle package with watchdog and deterministic replay.
- Added CUDA/ROS 2 containers, release automation, a public audit, and a
  deterministic reproducibility archive with benchmark, ablation, and failure
  gallery records.

See [the v0.3.0 release notes](RELEASE_NOTES_v0.3.0.md) for audited results and
known limits.
