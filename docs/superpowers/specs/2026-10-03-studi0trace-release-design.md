# Studi0Trace for Mac: hardening, release and retirement (plan 4)

2026-10-03. Plan 4 of the roadmap (`2026-09-24-studi0trace-local-app-design.md`). Tim asked for the Mac app to be
carried "until complete and bug free", autonomously. Plan 2 built the app; this plan makes it releasable: the bugs a
hunt over the shipped code found, a build anyone can download from GitHub, an app that updates itself, the licences
it owes, and the server it replaces retired.

## Decisions

| question | decision | why |
|---|---|---|
| Bugs first | a bug hunt (Rust shell, frontend, the built app driven by hand) before any release work; every Critical and Important finding fixed with a test, the deferred minors of plan 2's ledger triaged with it | a release is the wrong time to find them; an update reaches nobody who never installed |
| Architecture | universal (`universal-apple-darwin`: arm64 + x86_64), macOS 13+ | Intel Macs still run 13–15; plan 2 left the choice here. The core's fixtures hold to the bit on arm64 and to tolerance elsewhere, so an Intel trace is as good, and the tests run on arm64 |
| Signing | **ad-hoc only** (`bundle.macOS.signingIdentity: "-"`); no Developer ID, no notarization | `studi0trace-mac-app-distribution`. An arm64 binary must carry a signature to run at all; ad-hoc is free. The README tells people how to open it (System Settings → Privacy & Security → Open Anyway, or `xattr -dr com.apple.quarantine`) |
| CI | `.github/workflows/ci.yml` on push and pull request: frontend tests and build, then `cargo test --workspace --release`, on `macos-15` (arm64) | the shipped code is tested where the fixtures are exact. The Python suite stays a developer's job: it needs `uv`, maturin and the corpus |
| Release | `.github/workflows/release.yml` on a `v*` tag (and by hand): `tauri-apps/tauri-action` builds the universal app, dmg and updater archive, signs the archive with the updater key and attaches all of them and `latest.json` to a **draft** release | a person reads the draft and publishes it; nothing reaches users by a push alone |
| Updater | `tauri-plugin-updater` 2, driven from Rust only (the webview's capabilities stay `core:default` + start-dragging). Endpoint `https://github.com/timothynice/tracer/releases/latest/download/latest.json`. Studi0Trace ▸ Check for Updates…; a check at launch when the new setting **Check for updates automatically** is on (default on), silent unless there is one. A found update is a native dialog with the version and notes: Install and Relaunch / Later | GitHub serves the newest published release's `latest.json`; the minisign key pair is the updater's own and needs no Apple certificate |
| Updater key | generated 2026-10-03 at `~/.tauri/studi0trace.key` (no password; never in the repo); its public half in `tauri.conf.json`. Tim adds the private key as the repo secret `TAURI_SIGNING_PRIVATE_KEY` | an agent does not put keys into services |
| Versions | one version in `package.json`, the crate's `Cargo.toml` and `tauri.conf.json`; a test holds the three equal and `apps/desktop/scripts/version.sh X.Y.Z` sets them | the release tag, the updater manifest and the About panel read different files |
| Licences | `cargo about` over the desktop crate's dependency tree and a script over the frontend's production npm tree write one `THIRD_PARTY_NOTICES.html`, bundled as a resource; Help ▸ Acknowledgements opens it. The release workflow regenerates it | MIT obliges us to carry the notices of what we ship |
| Retirement | `render.yaml`, `backend/Dockerfile`, its standalone `backend/vexel-rs/Cargo.lock`, `tools/sync_vexel_lock.py` and its lock test go. Potrace and VTracer leave the server's registry and stay as bench adapters | the roadmap: Potrace and VTracer leave the product; nothing Python is deployed |
| FastAPI app | **kept, as a developer tool** (not deployed): the roadmap said remove it, but `tools/export_core_fixtures.py` exports the core's `api` and `auto` fixtures from the real routes, and the frontend's `web` platform is the browser harness the UI is checked in | removing it would freeze the core's contract fixtures with nothing to regenerate them from. It ships in nothing, which is what the roadmap's "nothing Python ships" asks |
| Docs | README leads with the app (download, first open, what it does, build from source); the server, API, bench and engine sections move below or into CONTRIBUTING; CLAUDE.md follows | the README is the download page |

## Not in plan 4

The web version on WebAssembly (plan 3, dropped while the product is Mac only), signing with a Developer ID, the App
Store, Homebrew casks, crash reporting, telemetry.
