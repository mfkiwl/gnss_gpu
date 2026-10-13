# Changelog

## Unreleased

- **RB-FGO-PF technical report** (`docs/rbpf_fgo_report.md`): method,
  evaluation protocol, results WP43–WP50, negative results (learned labels,
  wide-lane, more particles), limits and next steps.
  `experiments/rbpf_fgo/score_label.py` scores the `rule_fix` label (the
  report's FIX columns).
- **RB-FGO-PF WP50** (`experiments/rbpf_fgo/`): the PF decides integer sets,
  and carrier continuity carries them. `run_ship.sh` now runs the WP48 filter
  with a hysteresis label (`--fix-hyst 5,2,2,6,2,6,0.1,5`: enter after the
  WP48 rule held 5 epochs, stay while 6+ pairs agree above 0.90) and a
  forward integer carry (`--carry 1`, `RBPF_CARRY=1`, `integer_carry.py`).
  The carry keeps the last FIX's single-difference integers while the raw DD
  carrier fits them (8+ pairs, 0.15 cycle), and reports those epochs FIX at
  the carried-integer position. `integer_smoother.py` runs the same carry
  backward for a 15 s fixed-lag output. Correct FIX (3 seeds), real time:
  Tokyo 61.8 / 82.5 / 77.9%, Nagoya 70.4 / 51.1 / 29.7%. With the 15 s lag:
  65.3 / 85.0 / 81.1%, 74.6 / 54.2 / 38.4%. Every seed stays at or below
  0.20% wrong (WP48: 56.4 / 78.7 / 74.8 / 66.5 / 47.8 / 27.4%, worst 0.62%).
  `replay_carry.py` replays the label layer on a row-dump run.
  `RBPF_SHIP=wp48` reproduces the previous release.
- **RB-FGO-PF WP49** (`experiments/rbpf_fgo/`, negative result): wide-lane
  diagnostics, default off. `RBPF_WL=1` adds a per-epoch wide-lane LAMBDA
  fix and WL carrier position. `RBPF_WLPF=1` runs a second PF on the
  wide-lane problem, with WL integer sets held over time. Neither labels
  enough extra correct fixes at or below 1% wrong (about +1 pp; up to 48%
  wrong where the shared float is biased), so the shipped WP48 configuration
  is unchanged (D-047).
- **RB-FGO-PF WP48** (`experiments/rbpf_fgo/`): measurement model of the
  shared float graph. BeiDou GEO Doppler rows were 3–22 m/s off the
  reference velocity and biased the Doppler velocity prior (about 0.4 m/s
  vertical). The prior was also applied to the body velocity without the
  lever-arm rotation term. `DOP_V2=1` solves the Doppler velocity from
  per-satellite rows without GLONASS and BeiDou GEO, C/N0-weighted, and uses
  the solve's own covariance (`DOPPLER_LEVER_COMP=1` adds the lever-arm
  term). Horizontal p95 velocity error goes from about 2.0 to 0.4–0.5 m/s.
  WP48 also turns on Huber losses (`HUBER_PR=1.345`, `HUBER_CP=2.0`) and a
  C/N0 pseudorange variance term (`PR_CNR_ERR=0.5`). The label is
  re-selected so that every seed stays at or below 0.7% wrong
  (`--fix-rule 5,15,5,15,0.1,5,1.0`; the new 7th field vetoes labels while
  the DDPR quality exceeds 1.0). Correct FIX (3 seeds): Tokyo
  56.4 / 78.7 / 74.8%, Nagoya 66.5 / 47.8 / 27.4% (worst seed 0.62% wrong).
  PPC OFFICIAL: Tokyo 84.3 / 93.8 / 91.8%, Nagoya 74.5 / 56.1 / 78.8%
  (WP47: 80.5 / 91.8 / 88.3%, 66.9 / 55.1 / 71.0%). `RBPF_SHIP=wp47`
  reproduces the previous release.
- **RB-FGO-PF WP47** (`experiments/rbpf_fgo/`): Nagoya 3. Too few
  satellites kept both bands in Sakae, so the shared graph often had too few
  carrier pairs. `run_ship.sh` now keeps satellites with one usable band as
  code-only rows (`QC_ANY_BAND`, `QC_ANY_BAND_CODE_ONLY`,
  `QC_ANY_BAND_PR_SCALE=10`), puts both receivers on a 10° mask
  (`QC_BASE_MASK`, `ELMIN_DEG=10`), and re-selects the label over all six
  runs (`--fix-rule 5,15,3,99,0.1,9`). One-band satellites are never the DD
  reference, and their pair residuals are charged to them alone. Charging
  them to the reference as well gated or quarantined the reference and cost
  Tokyo 1 up to 12 pp. Correct FIX (3 seeds): Tokyo 56.9 / 78.0 / 76.1%,
  Nagoya 65.4 / 46.3 / 26.3%. Wrong FIX stays at or below 1% of fixes on
  every run and seed (worst 0.99%, Nagoya 2). PPC OFFICIAL on Nagoya 3 goes
  from 20.2% to 71.0%, and on Tokyo 3 from 90.4% to 88.3%.
  `RBPF_SHIP=wp46` reproduces the previous release.
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
