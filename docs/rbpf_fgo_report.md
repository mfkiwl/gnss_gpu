# RB-FGO-PF technical report: integer-ambiguity particle filtering on a shared factor graph

Status as of WP50 (2026-10-13). Code: [`experiments/rbpf_fgo/`](../experiments/rbpf_fgo/README.md).
Run-by-run record: [`internal_docs/inuex35_tc_fgo_benchmark.md`](../internal_docs/inuex35_tc_fgo_benchmark.md).
Decisions: D-040 to D-048 in [`internal_docs/decisions.md`](../internal_docs/decisions.md).

## Summary

RB-FGO-PF is a Rao-Blackwellized particle filter for urban RTK. One fixed-lag
GNSS/IMU factor graph (GTSAM) keeps every carrier ambiguity float. The
particles carry only integer hypotheses ("basins"), so a wrong fix competes
with the right one through accumulated likelihood instead of being locked in
by a single ratio test. A label layer decides when to report FIX: a
hysteresis rule on the particles' per-pair agreement, then an integer carry
that keeps an accepted integer set while the carrier stays locked.

On the six PPC 2024 runs (Tokyo 1–3, Nagoya 1–3), three seeds each, with one
scorer for every system:

| Correct FIX / rover epochs (wrong FIX / FIX) | Tokyo 1 | Tokyo 2 | Tokyo 3 | Nagoya 1 | Nagoya 2 | Nagoya 3 |
|---|---:|---:|---:|---:|---:|---:|
| inuex35 TC-FGO (starting point, local reproduction) | 46.7% (5.72%) | 59.7% (1.78%) | 58.2% (2.06%) | – | – | – |
| libgnss++ GTSAM FGO | 37.7% (29.9%) | 77.3% (1.57%) | 68.2% (1.56%) | – | – | – |
| libgnss++ KF-RTK (`ppc-demo`, low-cost preset) | 68.0% (8.76%) | 76.7% (1.80%) | 71.6% (3.01%) | 64.9% (14.8%) | 48.6% (2.15%) | 29.8% (29.4%) |
| **RB-FGO-PF WP50, real time** | **61.8% (0.04%)** | **82.5% (0.02%)** | **77.9% (0.00%)** | **70.4% (0.00%)** | **51.1% (0.14%)** | **29.7% (0.04%)** |
| **RB-FGO-PF WP50, 15 s fixed lag** | **65.3% (0.04%)** | **85.0% (0.02%)** | **81.1% (0.00%)** | **74.6% (0.00%)** | **54.2% (0.13%)** | **38.4% (0.03%)** |

Every seed of every run stays at or below 0.20% wrong (worst: Nagoya 2). The
real-time output never uses future data. The fixed-lag output also carries
integer sets backward from fixes up to 15 s later.

<p align="center">
  <img src="assets/figures/rbpf_fgo_tokyo.svg" alt="Correct FIX and wrong-FIX share of RB-FGO-PF WP50 (real time and 15 s fixed lag) and libgnss++ KF-RTK on the six Tokyo and Nagoya PPC runs" width="900">
</p>

## Evaluation protocol

- **Data.** PPC 2024 public runs: Septentrio mosaic-X5 rover at 5 Hz, 1 Hz
  base, 100 Hz MEMS IMU, 5 Hz ground truth. Tokyo 1–3 and Nagoya 1–3.
- **Correct FIX.** An epoch labelled FIX whose 3D error is under 0.5 m. The
  denominator is every rover epoch, including outages.
- **Wrong FIX.** A FIX with 3D error of 0.5 m or more, as a share of all FIX.
  The target was at most 1% per run and seed.
- **Seeds.** RB-FGO-PF is stochastic, so every configuration is run with
  three seeds (20260710–12). Single-seed differences on Nagoya are often
  within the ±3 pp seed spread, so decisions use three seeds.
- **PPC OFFICIAL.** The challenge's position-only score (distance share
  within 0.5 m), independent of the FIX label.
- **External engines.** libgnss++ and the inuex35 reproduction are scored by
  the same script. No external engine feeds the RB-FGO-PF FIX label.

## Method

### Shared float graph

A fixed-lag smoother holds pose, velocity and IMU bias per epoch
(CombinedImuFactor preintegration), DD pseudorange and DD carrier factors with
per-satellite single-difference ambiguity states, non-holonomic and
zero-velocity constraints, and a Doppler velocity prior. Integers are never
fixed inside the graph, except through PF feedback (below).

Measurement model as shipped:

- **Satellite set (WP45).** Two bands per system, BeiDou B1I + B3I, GLONASS
  off. With three bands requested, cssrlib's quality check had dropped every
  satellite missing any one band (Tokyo run1: 15 of 25 usable satellites).
- **One-band satellites (WP47).** A satellite with one usable band adds its
  pseudorange only (sigma ×10), is never the DD reference, and its pair
  residuals are charged to it alone. Charging them to the reference as well
  had gated or quarantined the reference and dropped every carrier pair of
  its system.
- **Robust weighting (WP48).** Huber losses on DD pseudorange and carrier,
  and a C/N0 term in the pseudorange variance.
- **Doppler (WP48).** A per-satellite Doppler least-squares solve without
  GLONASS and BeiDou GEO, C/N0-weighted, with the lever-arm rotation term and
  its own covariance as the prior. BeiDou GEO Doppler was 3–22 m/s off the
  reference velocity and had biased the old prior by about 0.4 m/s
  vertically. Horizontal p95 velocity error: 2.0 → 0.4–0.5 m/s.

### Particles over integer basins

Each epoch, the graph's joint marginal of position and DD ambiguities gives a
float `(y, Q)`. MLAMBDA proposes the top-K integer candidates (K = 12), and
64 particles move: survive, extend a basin by conditional rounding, adopt a
consensus subset or a full candidate, or release. Each particle's weight
gains the tempered likelihood of the epoch's DD pseudorange and carrier
under its integers (epoch-local robust least squares). Particles are
resampled when the effective sample size drops below N/2. Integers the
particles agree on are held in the shared graph, and released when the PF
withdraws its mass. Each lineage keeps its own IMU-predicted position
belief; the MAP basin's belief is the reported position (WP43). Slips and
outages in the graph remove the affected ambiguity from every particle in
the same epoch. Basins survive graph re-seeds (WP45).

### FIX label (WP50)

A wrong basin is the true trajectory shifted by a fixed offset. While the
satellite geometry is static, carrier phase cannot reveal it, and in urban
canyons the code is biased the same way for tens of seconds. A separately
tuned KF-RTK route in this repository (libgnss++ WP176, Tokyo run1 54.4% /
Nagoya run1 69.4% correct FIX at ~0% wrong) reaches the same wall. What
per-epoch labels ignored is time:

- Wrong fixes come in short flickers. Dropping fixes in runs shorter than
  2 s cut Nagoya 2's wrong share from 0.54% to 0.04%.
- A correct integer set stays valid for as long as the carrier stays locked.

The label therefore has three parts:

1. **Entry.** At least 15 DD pairs whose MAP integer holds more than 0.99
   of the particle mass (among all particles, or among the non-empty basins
   with mass 0.1+), or the graph's own LAMBDA fix with 5+ ambiguities. The
   DD-pseudorange quality must be at most 1.0, and the PF's position guards
   must agree. This must hold for 5 consecutive epochs (1 s).
2. **Stay.** FIX continues while 6+ pairs agree above 0.90, bridging up to
   2 failed epochs.
3. **Integer carry.** When the label drops, the last FIX epoch's
   single-difference integers are kept while a carrier-only least squares
   still fits them: 8+ pairs, 0.15-cycle slip check, new satellites admitted
   within 0.10 cycle. The epoch is reported FIX at the carried-integer
   position. `integer_smoother.py` runs the same carry backward from later
   fixes, which gives the 15 s fixed-lag output.

The carry is seeded only from hysteresis-labelled epochs. Seeded from the
plain entry rule, it propagated that rule's wrong fixes (Nagoya 2: 146 wrong
carried epochs, seed 20260710). Seeded from the hysteresis label, no carried
epoch came from a wrong source. The label layer does not feed back into the
PF, so it can be replayed on a recorded run (`replay_carry.py`); the replay
is bit-identical to the live run.

## How the results moved

| Release | Change | Correct FIX (3 seeds) | Worst wrong / FIX |
|---|---|---|---|
| WP43 | lineage-tracked output | Tokyo 35.4 / 64.1 / 68.5% | 0.33% |
| WP45 | two-band satellite set, basin persistence, label from PF agreement | Tokyo 66.6 / 83.9 / 84.3% | Tokyo 0.56%, but Nagoya 2/3 8.5 / 16.2% |
| WP46 | label re-selected on Tokyo and Nagoya | 56.1 / 76.4 / 75.8 / 61.0 / 46.5 / 12.3% | Nagoya 3 6.2% |
| WP47 | one-band satellites, residual-attribution fix | 56.9 / 78.0 / 76.1 / 65.4 / 46.3 / 26.3% | 0.99% |
| WP48 | Doppler v2, Huber, C/N0 weighting | 56.4 / 78.7 / 74.8 / 66.5 / 47.8 / 27.4% | 0.62% |
| WP50 | hysteresis label, integer carry | 61.8 / 82.5 / 77.9 / 70.4 / 51.1 / 29.7% | 0.20% |
| WP50, 15 s lag | backward carry | 65.3 / 85.0 / 81.1 / 74.6 / 54.2 / 38.4% | 0.19% |

PPC OFFICIAL (position only): WP46 80.8 / 92.7 / 90.4 / 65.4 / 55.3 / 20.2%;
WP50 84.3 / 93.8 / 91.8 / 74.5 / 56.1 / 78.8%; WP50 with the 15 s lag
84.6 / 94.2 / 92.4 / 75.0 / 57.8 / 79.4%.

## What did not work

Each of these was measured on the same runs and seeds, and is recorded in the
benchmark record:

- **Learned labels.** A gradient-boosting classifier on 60+ causal PF
  features (nested leave-one-run-out) labelled fewer fixes than the rule at
  the same wrong-FIX cap (Tokyo 1: 52.3 vs 56.9%). Agreement-fraction terms
  added 0.3–1.4 pp held out.
- **External witness in the label.** A libgnss++ KF-RTK witness raised the
  FIX rate, but it was removed: the label must come from the PF itself.
- **Wide-lane fixes (WP49).** The WL position is within 0.5 m on 7–30 pp more
  epochs than the FIX label. Neither a per-epoch WL ratio test nor a second
  PF holding WL integer sets over time separates right from wrong WL sets
  where the float is biased (up to 48% wrong on Nagoya 2). WL also does not
  veto Nagoya 2's wrong basin: its position is biased the same way.
- **More particles, tracked-belief weighting.** N = 256 and weighting by the
  tracked position belief did not help.
- **Tighter Doppler before the GEO fix.** It cost up to 6 pp, which is what
  exposed the BeiDou GEO Doppler error.
- **Float-side tweaks alone.** Huber, C/N0 weighting and Doppler v2 cut the
  share of epochs with the float more than 1 m off, but each moved FIX by
  only ±1–2 pp on its own. Together they raised PPC OFFICIAL by 1–8 pp.

## Limits

- **In-sample label selection.** The entry and hysteresis rules were chosen
  on the same six runs. Leave-one-run-out, 5 of 6 folds pick the shipped
  rule and stay at or below 0.01% wrong held out. The Nagoya 2 fold picks a
  1-epoch entry and gets 2.6% wrong, because nothing in its training runs
  shows why the entry delay is needed. The 1 s entry is fixed by design.
  No dataset outside PPC has been tested.
- **Long hard stretches.** Most remaining unfixed epochs sit in 1–7 minute
  stretches where the code-only position is 2–10 m off and carrier slips are
  frequent. Tokyo 1 has ~25% of its epochs in one such stretch, Nagoya 2
  ~35%, Nagoya 3 ~50%. The integer carry ends there because too few pairs
  stay consistent.
- **Nagoya 2.** A self-consistent basin about 0.95 m off (556840–556862 s)
  remains the main source of wrong fixes.
- **Compute.** CPU only. The PF step takes 80–90 ms; full runs process 5–7.5
  epochs/s with three runs sharing a 4-core machine, against 5 Hz data. A
  GPU MLAMBDA path exists (`RBPF_CUDA=1`), but more particles did not raise
  accuracy.
- **Not tested.** 3D-map (PLATEAU) NLOS rejection: the building data was not
  reachable from the environment used.

## Next steps

1. A backward-in-time PF pass combined with the forward one, for the
   post-processed output. Unfixed epochs followed by a correct FIX within
   15 s (no data gap) are an upper bound of +11 to +28 pp; the backward
   integer carry recovers +2.5 to +8.7 pp of it.
2. Feeding carried integers back into the shared graph, so that the float
   stays anchored where the PF's agreement dips.
3. A held-out dataset for the label rule.

## Reproduction

```bash
cd experiments/rbpf_fgo
bash setup_deps.sh                       # GTSAM python, cssrlib fork
# Tokyo run 1, one seed: results/wp50_r1/run1.{pos,npz} and run1_rows.npz
PPC_DATA_ROOT=.../PPC-Dataset/tokyo bash run_ship.sh wp50_r1 1 0 --rb-seed 20260710
# Nagoya run 3 (lever arm and scorers follow PPC_CITY)
PPC_CITY=nagoya PPC_DATA_ROOT=.../PPC-Dataset/nagoya bash run_ship.sh wp50ng_r3 3 0 --rb-seed 20260710
# 15 s fixed-lag output of the same run: run1_smooth.{npz,pos}
python integer_smoother.py results/wp50_r1/run1.npz --max-s 15
# FIX columns (correct FIX, wrong / FIX)
python score_label.py --data-root .../PPC-Dataset results/wp50_r1/run1.npz results/wp50_r1/run1_smooth.npz
# PPC OFFICIAL (results/<prefix>_r{1,2,3})
python score_runs.py --data-root .../PPC-Dataset wp50
PPC_CITY=nagoya python score_runs.py --data-root .../PPC-Dataset wp50ng
```

`RBPF_SHIP=wp48`, `wp47`, `wp46`, `wp45` and `wp43` reproduce the earlier
releases.
