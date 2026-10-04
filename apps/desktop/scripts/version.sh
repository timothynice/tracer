#!/usr/bin/env bash
# Sets the app's one version: apps/desktop/package.json (and its lockfile),
# src-tauri/tauri.conf.json, src-tauri/Cargo.toml, then Cargo.lock follows.
#   bash apps/desktop/scripts/version.sh X.Y.Z
set -euo pipefail

if [ "$#" -ne 1 ] || ! [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: version.sh X.Y.Z" >&2
  exit 2
fi
VERSION="$1"

cd "$(dirname "$0")/../../.."
DESKTOP=apps/desktop
CARGO_TOML="$DESKTOP/src-tauri/Cargo.toml"

# package.json and its lockfile: parse, set, write back at 2 spaces with a trailing newline.
node -e '
const fs = require("fs");
const [version, ...files] = process.argv.slice(1);
for (const file of files) {
  const doc = JSON.parse(fs.readFileSync(file, "utf8"));
  doc.version = version;
  if (doc.packages && doc.packages[""]) doc.packages[""].version = version;
  fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n");
}
' "$VERSION" "$DESKTOP/package.json" "$DESKTOP/package-lock.json"

# tauri.conf.json is laid out by hand (compact arrays), which a stringify would
# reflow: replace its top-level "version" line in place, then check it still parses.
node -e '
const fs = require("fs");
const [version, file] = process.argv.slice(1);
const text = fs.readFileSync(file, "utf8");
const next = text.replace(/^  "version": "[^"]*"/m, `  "version": "${version}"`);
if (JSON.parse(next).version !== version) throw new Error(file + ": no top-level version to set");
fs.writeFileSync(file, next);
' "$VERSION" "$DESKTOP/src-tauri/tauri.conf.json"

# Cargo.toml: the first `version =` line only (the [package] table's).
tmp="$(mktemp)"
awk -v v="$VERSION" '
  !done && /^version[[:space:]]*=/ { print "version = \"" v "\""; done = 1; next }
  { print }
' "$CARGO_TOML" > "$tmp"
cat "$tmp" > "$CARGO_TOML"
rm -f "$tmp"

cargo update -p studi0trace-desktop --offline

echo "package.json:    $(node -p 'require("./'"$DESKTOP"'/package.json").version')"
echo "tauri.conf.json: $(node -p 'require("./'"$DESKTOP"'/src-tauri/tauri.conf.json").version')"
echo "Cargo.toml:      $(awk -F'"' '/^version[[:space:]]*=/ { print $2; exit }' "$CARGO_TOML")"
