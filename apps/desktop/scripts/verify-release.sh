#!/usr/bin/env bash
# Verify the actual files that users download, including the updater archive.
set -euo pipefail
cd "$(dirname "$0")/.."
root="$(cd ../.. && pwd)"
bundle="${1:-${CARGO_TARGET_DIR:-$root/target}/universal-apple-darwin/release/bundle}"
version="$(node -p 'require("./src-tauri/tauri.conf.json").version')"
app="$bundle/macos/Studi0Trace.app"
dmg="$bundle/dmg/Studi0Trace_${version}_universal.dmg"
archive="$bundle/macos/Studi0Trace.app.tar.gz"

verify_signature() {
  local metadata
  codesign --verify --deep --strict --verbose=2 "$1"
  metadata="$(codesign --display --verbose=4 "$1" 2>&1)"
  grep -Fq 'Authority=Developer ID Application: Timothy Nice (B7VLZJMQAL)' <<< "$metadata"
  grep -Fxq 'TeamIdentifier=B7VLZJMQAL' <<< "$metadata"
  grep -q '^Timestamp=' <<< "$metadata"
}

verify_app() {
  local metadata assessment
  verify_signature "$1"
  metadata="$(codesign --display --verbose=4 "$1" 2>&1)"
  grep -q 'flags=.*runtime' <<< "$metadata"
  lipo "$1/Contents/MacOS/studi0trace-desktop" -verify_arch arm64 x86_64
  xcrun stapler validate "$1"
  assessment="$(spctl --assess --type execute --verbose=4 "$1" 2>&1)"
  printf '%s\n' "$assessment"
  grep -Fq 'source=Notarized Developer ID' <<< "$assessment"
}

verify_app "$app"
verify_signature "$dmg"
xcrun stapler validate "$dmg"
spctl --assess --type open --context context:primary-signature --verbose=4 "$dmg"

[ -s "$archive" ] && [ -s "$archive.sig" ]
temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT
tar -xzf "$archive" -C "$temp"
verify_app "$temp/Studi0Trace.app"
diff -qr "$app" "$temp/Studi0Trace.app"
echo "release: app, universal updater archive and DMG are signed, notarized and accepted by Gatekeeper"
