#!/usr/bin/env bash
# Run the shipped RB-FGO-PF configuration (WP18 + output guards + the WP43
# output layer + the WP45 satellite set, basin persistence and FIX label
# rule, made stricter in WP46) on one PPC run (PPC_CITY=tokyo | nagoya).
# RBPF_SHIP=wp45 / wp43 reproduce the earlier releases;
# RBPF_SHIP=wp18 is equivalent to repro_tc_fgo/results/wp18/run_rbpf18.sh.
#
#   PPC_DATA_ROOT=.../PPC-Dataset-data/tokyo \
#   bash run_ship.sh <name> <run 1|2|3> <max_epochs> [extra wp16_run_rbpf.py args]
#
# Writes results/<name>/run<N>.{pos,npz} and results/<name>.log. With WP45 the
# .pos FIX flag is the --fix-rule label. The WP43/WP18 tables additionally
# relabel fixes below the report floor as float:
#   python relabel_nb9_pos.py results/<name>/runN.pos results/<name>/runN.npz out.pos 12
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="$1"; RUN="$2"; MAXEP="$3"; shift 3
PY="${RBPF_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$HERE/.venv/Scripts/python.exe" ]; then PY="$HERE/.venv/Scripts/python.exe"; else PY="$HERE/.venv/bin/python"; fi
fi

GUARDS="--env RBPF_FIX_VOTE_DD=6.0 --env RBPF_FIX_VOTE_DPR=3.5 --env RBPF_FB_COMMIT_MAX_DPR=1.5"
# WP43 output layer (2026-10-10): tracked lineage beliefs as the reported
# position and the dead-reckoned gap fill. RBPF_SHIP=wp18 reproduces the
# WP18 output (FIX decisions are the same either way).
WP43="--dr-fill 1 --env RBPF_TRACK=2 --env RBPF_TRACK_EMPTY=1 --env RBPF_TRACK_OUT=2"
SHIP="${RBPF_SHIP:-wp46}"
if [ "$SHIP" = "wp18" ]; then WP43=""; fi
# WP45 (2026-10-12): every satellite with two usable bands (--nf 3 made
# cssrlib's qcedit drop any satellite without L5), BeiDou B1I+B3I instead
# of BDS-3-only B1C, GLONASS off; basins survive graph re-seeds
# (RBPF_KEEP); the .pos FIX flag is the leave-one-run-out selected label
# rule (fix_rule_loro.py).
# WP46 (2026-10-12): the same filter with a stricter label rule selected
# leave-one-run-out over Tokyo 1-3 and Nagoya 1-2 (WP45's rule, selected
# on Tokyo only, labelled 8.5% wrong fixes on Nagoya 2).
WP45=""
if [ "$SHIP" = "wp45" ]; then
  WP45="--nf 2 --band-priority C:2,6;R:- --env RBPF_KEEP=1 --fix-rule 3,5,3,12,0.1,5"
elif [ "$SHIP" = "wp46" ]; then
  WP45="--nf 2 --band-priority C:2,6;R:- --env RBPF_KEEP=1 --fix-rule 3,15,3,15,0.1,99"
fi
# PPC_CITY=nagoya: the Nagoya GNSS antenna lever arm (README, IMU FRD
# 0.593,-0.670,-1.216 m) as the runner's body FLU vector
CITYFLAGS=""
if [ "${PPC_CITY:-tokyo}" = "nagoya" ]; then
  CITYFLAGS="--env LEVER_ARM=0.593,0.670,1.216"
fi
R1FLAGS=""
# WP43/WP18 only: run1-specific WP13r recovery flags (sanity reset +
# post-reset carrier-phase hold); RBPF_R1FLAGS=0 drops them. WP45 runs one
# configuration on every run (with the sanity reset on run1 it scores
# within the seed spread: 67.1 vs 66.6% correct FIX); so does WP46.
if [ "$RUN" = "1" ] && [ "${RBPF_R1FLAGS:-1}" != "0" ] && [ "$SHIP" != "wp45" ] && [ "$SHIP" != "wp46" ]; then
  R1FLAGS="--sanity-enable 1 --env SANITY_PR_ONLY=1 --env RECOV_CP_HOLD_EPOCHS=5 --env RECOV_CP_RELEASE_THRESH=2.0 --env RECOV_CP_RELEASE_COUNT=5 --env SANITY_MAX_MEDIAN_RATIO=5.0"
fi
mkdir -p "$HERE/results"
# shellcheck disable=SC2086
"$PY" "$HERE/wp16_run_rbpf.py" "$RUN" --max-ep "$MAXEP" \
  --out-dir "$HERE/results/$NAME" \
  --env SEED_N_CYCLES=1 --env COND_HOLD=1 --env RBPF_FB=1 --env RBPF_SPAWN=1 \
  $R1FLAGS $GUARDS $WP43 $WP45 $CITYFLAGS "$@" > "$HERE/results/$NAME.log" 2>&1
tail -n 6 "$HERE/results/$NAME.log"
