#!/usr/bin/env bash
# The three speeds of the focus loop. Run from backend/.
#   tools/qloop.sh focus [bench.focus run args]   ~5 s: the one asset (default bench/focus/wave-lockup)
#   tools/qloop.sh sentinels                      ~30 s: bench/sentinels.txt, gated per item
#   tools/qloop.sh full                           minutes: corpus + held-out + degraded, gated per item
#   tools/qloop.sh ref [SET ...]                  freeze the references the gates compare against (default: all sets)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
KEEP=bench/reports/keep-2026-10-05-wave-lockup
ASSET=${ASSET:-bench/focus/wave-lockup}
SETS="corpus heldout degraded"  # bench/<set>, each gated against $KEEP/ref-<set>
export VEXEL_BACKEND=${VEXEL_BACKEND:-rust} RAYON_NUM_THREADS=${RAYON_NUM_THREADS:-2}
W=${WORKERS:-6}

# bench/sentinels.txt: the first column names the set (corpus | heldout | degraded), the second the item id
ids() { grep -v '^#' bench/sentinels.txt | awk -v c="$1" '$1==c {print $2}' | paste -sd, -; }

bench_run() {  # corpus-dir ids-or-empty out-dir
  local extra=(); [ -n "$2" ] && extra=(--ids "$2")
  # ${extra[@]+...}: an empty array is "unbound" under set -u on bash 3.2 (macOS)
  $PY -m bench run --engines vexel --corpus "$1" ${extra[@]+"${extra[@]}"} --no-media --workers "$W" --out "$3" >/dev/null
}

case "${1:-}" in
  focus) shift; exec $PY -m bench.focus run "$ASSET" "$@" ;;
  ref)
    shift
    for set in ${*:-$SETS}; do
      bench_run "bench/$set" "" "$KEEP/ref-$set"
      echo "reference: $KEEP/ref-$set/results.json"
    done ;;
  sentinels|full)
    tag=$(date +%H%M%S); rc=0
    for set in $SETS; do
      sel=""
      if [ "$1" = sentinels ]; then
        sel=$(ids $set)
        [ -n "$sel" ] || { echo "qloop: no $set ids in bench/sentinels.txt" >&2; exit 1; }
      fi
      bench_run "bench/$set" "$sel" "$KEEP/$1-$tag-$set"
      echo "== $set"; $PY -m bench.gate "$KEEP/ref-$set/results.json" "$KEEP/$1-$tag-$set/results.json" || rc=1
    done
    exit $rc ;;
  *) sed -n '2,6p' "$0"; exit 2 ;;
esac
