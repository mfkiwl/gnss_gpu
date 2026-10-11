#!/usr/bin/env bash
# Run RB-FGO-PF jobs from a queue file with bounded concurrency.
#
#   PPC_DATA_ROOT=... bash run_queue.sh <queue file> [parallel=4]
#
# Each queue line: <name> <run> <max_epochs> [extra wp16_run_rbpf.py args]
# (the run_ship.sh argument order). Lines starting with # are skipped.
# A job whose results/<name>/run<N>.npz already holds a finished run
# (its log ends with "Wrote") is not rerun.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEUE="$1"; PAR="${2:-4}"
running=0
while read -r name run maxep rest; do
  [ -z "${name:-}" ] && continue
  case "$name" in \#*) continue ;; esac
  if [ -f "$HERE/results/$name.log" ] && grep -q "^Wrote" "$HERE/results/$name.log"; then
    echo "[queue] skip $name (done)"; continue
  fi
  while [ "$(jobs -rp | wc -l)" -ge "$PAR" ]; do sleep 5; done
  echo "[queue] start $name run$run $maxep $rest"
  # shellcheck disable=SC2086
  ( bash "$HERE/run_ship.sh" "$name" "$run" "$maxep" --checkpoint-every 0 $rest >/dev/null 2>&1;
    echo "[queue] done $name: $(grep -E '^WP16 SUMMARY|Traceback|Error' "$HERE/results/$name.log" | tail -1)" ) &
done < "$QUEUE"
wait
echo "[queue] all done"
