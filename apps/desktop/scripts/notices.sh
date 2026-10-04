#!/usr/bin/env bash
# Regenerates apps/desktop/src-tauri/resources/THIRD_PARTY_NOTICES.html: the Rust crates the app is built from
# (cargo-about, configured by about.toml and about.hbs) and the frontend's production npm packages and fonts
# (npm-notices.mjs). Deterministic: the same lockfiles give the same bytes.
# Needs: cargo install cargo-about --locked --version 0.9.2 --features cli, and `npm ci` in frontend/.
set -euo pipefail
cd "$(dirname "$0")/../../.."

command -v cargo-about >/dev/null || { echo "notices: cargo-about is missing: cargo install cargo-about --locked --version 0.9.2 --features cli" >&2; exit 1; }
[ -d frontend/node_modules ] || { echo "notices: frontend/node_modules is missing: cd frontend && npm ci" >&2; exit 1; }

RUST="$(mktemp)"
trap 'rm -f "$RUST"' EXIT
cargo about generate --manifest-path apps/desktop/src-tauri/Cargo.toml -c apps/desktop/about.toml apps/desktop/about.hbs > "$RUST"
node apps/desktop/scripts/npm-notices.mjs "$RUST" apps/desktop/src-tauri/resources/THIRD_PARTY_NOTICES.html
