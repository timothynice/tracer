#!/usr/bin/env bash
# Build Studi0Trace.app, open a sample with it the way Finder does (`open -a`), and check that it answered:
#   1. a trace worker ran (a `--trace-worker` child of the instance this script started): the settings say
#      Trace new images straight away;
#   2. the sample is first in the recent files: the path reached the page, the page opened it, Rust kept it.
# The user's settings are put back afterwards, once the instance is gone. The script refuses to run while any
# Studi0Trace is already running (it would share the settings file). Run from apps/desktop: `npm run smoke`
# (SKIP_BUILD=1 to reuse a build; SMOKE_APP=<path to a .app> checks that bundle instead, e.g. the universal one, and skips the build).
set -euo pipefail
cd "$(dirname "$0")/.."

APP="${SMOKE_APP:-$(cd ../.. && pwd)/target/release/bundle/macos/Studi0Trace.app}"
SUPPORT="$HOME/Library/Application Support/com.studi0.trace"
SETTINGS="$SUPPORT/settings.json"
SAMPLE="$(cd ../../frontend/public/samples && pwd)/logo.png"

# Any Studi0Trace (the user's own, a lingering one) shares the settings file: do not start, touch nothing.
if pgrep -f "Studi0Trace.app/Contents/MacOS" >/dev/null; then
  echo "smoke: Studi0Trace is already running; quit it first (the smoke test rewrites its settings)." >&2
  exit 1
fi

[ "${SKIP_BUILD:-0}" = 1 ] || [ -n "${SMOKE_APP:-}" ] || npx tauri build --bundles app

mkdir -p "$SUPPORT"
BACKUP=""
PID=""
if [ -f "$SETTINGS" ]; then BACKUP="$(mktemp)"; cp "$SETTINGS" "$BACKUP"; fi

alive() { [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; }
wait_gone() { for _ in $(seq 1 $(( $1 * 10 ))); do alive || return 0; sleep 0.1; done; ! alive; }

restore() {
  local rc=$?
  set +e
  trap - EXIT INT TERM
  if [ -z "$PID" ]; then PID="$(pgrep -o -f "$APP/Contents/MacOS/" || true)"; fi
  if alive; then
    # the trace on open is not exported, so the quit asks first ("Quit Studi0Trace?"): answer Quit, the way a
    # person would (System Events needs Accessibility; without it the kill below still ends the instance)
    osascript -e 'tell application id "com.studi0.trace" to quit' >/dev/null 2>&1
    for _ in $(seq 1 30); do
      alive || break
      osascript -e "tell application \"System Events\" to tell (first process whose unix id is $PID) to click button \"Quit\" of sheet 1 of window 1" >/dev/null 2>&1 && break
      sleep 0.1
    done
    wait_gone 5 || {
      pkill -P "$PID" 2>/dev/null   # its trace workers
      kill "$PID" 2>/dev/null
      wait_gone 3 || kill -9 "$PID" 2>/dev/null
      wait_gone 2
    }
  fi
  pkill -f "$APP/Contents/MacOS/" 2>/dev/null   # anything of ours left over (workers)
  if alive; then
    echo "smoke: WARNING: Studi0Trace (pid $PID) is still running; your settings were NOT restored." >&2
    [ -n "$BACKUP" ] && echo "smoke: your settings are saved in $BACKUP; put them back with: cat '$BACKUP' > '$SETTINGS'" >&2
  elif [ -n "$BACKUP" ]; then
    if cat "$BACKUP" > "$SETTINGS"; then rm -f "$BACKUP"
    else echo "smoke: WARNING: could not restore your settings; they are saved in $BACKUP" >&2; fi
  else
    rm -f "$SETTINGS"
  fi
  exit "$rc"
}
trap restore EXIT
trap 'exit 130' INT TERM

printf '{"settings":{"appearance":"system","exportTo":"ask","revealAfterExport":false,"traceOnOpen":true,"liveUpdate":true,"checkForUpdates":false,"recent":[]}}' > "$SETTINGS"
open -n -a "$APP" "$SAMPLE"

worker=0
recent=0
for _ in $(seq 1 150); do
  [ -n "$PID" ] || PID="$(pgrep -o -f "$APP/Contents/MacOS/" || true)"
  if [ "$worker" = 0 ] && [ -n "$PID" ] && pgrep -P "$PID" -f -- "--trace-worker" >/dev/null; then worker=1; fi
  if [ "$recent" = 0 ] && grep -qF "\"$SAMPLE\"" "$SETTINGS" 2>/dev/null; then recent=1; fi
  [ "$worker" = 1 ] && [ "$recent" = 1 ] && break
  sleep 0.2
done
echo "trace worker ran:      $([ "$worker" = 1 ] && echo yes || echo NO)"
echo "opened from Finder:    $([ "$recent" = 1 ] && echo yes || echo NO)"
[ "$worker" = 1 ] && [ "$recent" = 1 ]
