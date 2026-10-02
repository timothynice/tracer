#!/usr/bin/env bash
# Build Studi0Trace.app, open a sample with it the way Finder does (`open -a`), and check that it answered:
#   1. a trace worker ran (`--trace-worker` appears in the process list): the settings say Trace new images straight away;
#   2. the sample is first in the recent files: the path reached the page, the page opened it, Rust kept it.
# The user's settings are put back afterwards. Run from apps/desktop: `npm run smoke` (SKIP_BUILD=1 to reuse a build).
set -euo pipefail
cd "$(dirname "$0")/.."

[ "${SKIP_BUILD:-0}" = 1 ] || npx tauri build --bundles app
APP="$(cd ../.. && pwd)/target/release/bundle/macos/Studi0Trace.app"
SUPPORT="$HOME/Library/Application Support/com.studi0.trace"
SETTINGS="$SUPPORT/settings.json"
SAMPLE="$(cd ../../frontend/public/samples && pwd)/logo.png"

mkdir -p "$SUPPORT"
BACKUP=""
if [ -f "$SETTINGS" ]; then BACKUP="$(mktemp)"; cp "$SETTINGS" "$BACKUP"; fi
restore() {
  osascript -e 'quit app "Studi0Trace"' >/dev/null 2>&1 || true
  # let it go before the settings come back: a quitting app must not write over them
  for _ in $(seq 1 50); do pgrep -f "Studi0Trace.app/Contents/MacOS" >/dev/null || break; sleep 0.1; done
  if [ -n "$BACKUP" ]; then mv "$BACKUP" "$SETTINGS"; else rm -f "$SETTINGS"; fi
}
trap restore EXIT

printf '{"settings":{"appearance":"system","exportTo":"ask","revealAfterExport":false,"traceOnOpen":true,"liveUpdate":true,"recent":[]}}' > "$SETTINGS"
open -n -a "$APP" "$SAMPLE"

worker=0
recent=0
for _ in $(seq 1 150); do
  if [ "$worker" = 0 ] && pgrep -f "studi0trace-desktop --trace-worker" >/dev/null; then worker=1; fi
  if [ "$recent" = 0 ] && grep -q "\"$SAMPLE\"" "$SETTINGS" 2>/dev/null; then recent=1; fi
  [ "$worker" = 1 ] && [ "$recent" = 1 ] && break
  sleep 0.2
done
echo "trace worker ran:      $([ "$worker" = 1 ] && echo yes || echo NO)"
echo "opened from Finder:    $([ "$recent" = 1 ] && echo yes || echo NO)"
[ "$worker" = 1 ] && [ "$recent" = 1 ]
