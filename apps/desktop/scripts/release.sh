#!/usr/bin/env bash
# Used locally and as tauri-action's build command. Uploads happen only after
# both the app and DMG pass Apple's signature, notarization and Gatekeeper checks.
set -euo pipefail
cd "$(dirname "$0")/.."

# tauri-action passes these arguments to locate the universal release artifacts.
# Keep the build fixed so --no-sign, --skip-stapling or another config cannot
# accidentally weaken a release.
if [ "${1:-}" != build ]; then
  echo "usage: release.sh build [--target universal-apple-darwin --config src-tauri/tauri.release.conf.json]" >&2
  exit 2
fi
shift
if [ "$#" -ne 0 ] && [ "$*" != '--target universal-apple-darwin --config src-tauri/tauri.release.conf.json' ]; then
  echo "release: only the universal target and release config are supported" >&2
  exit 2
fi

for name in APPLE_API_KEY APPLE_API_ISSUER APPLE_API_KEY_PATH TAURI_SIGNING_PRIVATE_KEY; do
  if [ -z "${!name:-}" ]; then
    echo "release: missing $name; refusing to build an unverified release" >&2
    exit 1
  fi
done
[ -s "$APPLE_API_KEY_PATH" ] || { echo "release: notarization key file is missing" >&2; exit 1; }
if [ -n "${APPLE_CERTIFICATE:-}" ] && [ -z "${APPLE_CERTIFICATE_PASSWORD:-}" ]; then
  echo "release: APPLE_CERTIFICATE_PASSWORD is required with APPLE_CERTIFICATE" >&2
  exit 1
fi

identity='Developer ID Application: Timothy Nice (B7VLZJMQAL)'
if [ -z "${APPLE_CERTIFICATE:-}" ] && ! security find-identity -v -p codesigning | grep -Fq "\"$identity\""; then
  echo "release: install the Developer ID identity or provide APPLE_CERTIFICATE and APPLE_CERTIFICATE_PASSWORD" >&2
  exit 1
fi
export APPLE_SIGNING_IDENTITY="$identity"
# create-dmg otherwise scripts Finder, which is unreliable on headless runners.
export CI=true
export TAURI_BUNDLER_DMG_IGNORE_CI=false

./node_modules/.bin/tauri build --target universal-apple-darwin --config src-tauri/tauri.release.conf.json -- --locked

root="$(cd ../.. && pwd)"
bundle="${CARGO_TARGET_DIR:-$root/target}/universal-apple-darwin/release/bundle"
version="$(node -p 'require("./src-tauri/tauri.conf.json").version')"
dmg="$bundle/dmg/Studi0Trace_${version}_universal.dmg"

# Tauri notarizes and staples the app, then signs the DMG. Submit the final DMG
# as well, so its own Gatekeeper assessment and offline ticket are verified.
auth=(--key "$APPLE_API_KEY_PATH" --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER")
report="$bundle/dmg/notarization.json"
if ! xcrun notarytool submit "$dmg" "${auth[@]}" --wait --timeout 30m --output-format json > "$report"; then
  cat "$report" >&2
  echo "release: DMG notarization failed or timed out; release assets will not be uploaded" >&2
  exit 1
fi
if ! node -e 'const r = require(process.argv[1]); if (r.status !== "Accepted") process.exit(1)' "$report"; then
  cat "$report" >&2
  echo "release: Apple did not accept the DMG; release assets will not be uploaded" >&2
  exit 1
fi
xcrun stapler staple "$dmg"
bash scripts/verify-release.sh "$bundle"
