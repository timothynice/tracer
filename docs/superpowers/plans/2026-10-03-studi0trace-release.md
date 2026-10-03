# Studi0Trace for Mac: hardening, release and retirement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Studi0Trace for Mac releasable: fix what the bug hunt found, build a universal ad-hoc-signed app on GitHub Actions with a self-updater and licence notices, and retire the server deploy.

**Architecture:** Spec `docs/superpowers/specs/2026-10-03-studi0trace-release-design.md`. Part A fixes bugs in `apps/desktop/src-tauri` and `frontend/src`, each with a regression test. Part B adds release machinery: one version, the updater (Rust only), licence notices, CI and release workflows, then removes the Render/Docker deploy and Potrace/VTracer from the product, and rewrites the docs.

**Tech Stack:** Tauri 2.12 (Rust 1.90+, `tauri-plugin-updater` 2.x), React 18 + TS + Vitest, GitHub Actions (`macos-15`, `tauri-apps/tauri-action@v0`), `cargo about` 0.9, Python 3.12 (`backend/.venv`, pytest).

## Global Constraints

- Mac only: macOS 13.0+, bundle id `com.studi0.trace`, product name `Studi0Trace`.
- Never signed with a Developer ID, never notarized. Ad-hoc only: `bundle.macOS.signingIdentity` = `"-"`.
- The webview's capabilities stay exactly `core:default` and `core:window:allow-start-dragging`. New native features (updater, acknowledgements) are driven from Rust; never add a plugin permission to `capabilities/default.json`.
- The frontend talks to its host only through `src/platform`; never `invoke`/`fetch` from a component.
- The updater endpoint is exactly `https://github.com/timothynice/tracer/releases/latest/download/latest.json`; the updater public key is exactly
  `dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDE1QTI0NDhGMThEQjlBNDMKUldSRG10c1lqMFNpRlNUN3hRNVNwSWZRcGM5SXlTNTZ0RlNjYlRxcGFkODJKZWJYYVpJWXpCVk0K`.
  The private key is at `~/.tauri/studi0trace.key` and is never read into, copied into or committed to the repo.
- Local builds must keep working without the private key: updater artifacts are switched on only by `apps/desktop/src-tauri/tauri.release.conf.json`, passed with `--config` in the release workflow.
- `crates/studi0trace-core` is not touched by this plan (its fixtures, its wasm-cleanliness).
- Tests: `cd frontend && npm run test:run` (all pass) and `npm run build`; `cargo test --workspace --release` from the root (needs `frontend/dist` built first); `cd backend && .venv/bin/python -m pytest -q` for Python changes.
- Clippy for the desktop crate: `cargo clippy -p studi0trace-desktop --release --no-deps -- -D warnings` stays clean.
- Never `rm -rf`; remove tracked files with `git rm`. Never a bare `git stash`. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Copy in the UI: sentence case, macOS terms (Settings, Show in Finder, ellipsis `…` on items that ask for more).

---

## Part A — Hardening

Found by the bug hunt of 2026-10-03 (reports, with scratch reproductions, in the controller's scratchpad:
`bughunt-rust/report.md`, `bughunt-frontend/report.md`, `qa-native/report.md`; the relevant entries are quoted in
each task brief below, so an implementer needs none of them). Every fix lands with a regression test that fails
before it. Ids like R-I1 / F-M4 name the report entries (R = Rust shell, F = frontend, Q = native QA).

### Task A1: Intake that never reads what it will refuse (Rust)

**Files:** `apps/desktop/src-tauri/src/intake.rs`, `src/store.rs`, `src/commands.rs` (only as needed), their tests.

- **R-I1** `open_path` reads the whole file (`std::fs::read`) before anything checks its size; a 3 GB drop
  peaks at 3.2 GB of memory, and TIFF/HEIC are read whole only to sniff 12 bytes. Fix: read a prefix of at most
  16 bytes to sniff. For a native kind, when `metadata().len()` exceeds the core's byte limit
  (`studi0trace_core::intake::Limits::default().max_bytes`), answer the core's own `too_large` error (same
  `code`, status and message shape as `core.upload` gives for too many bytes — build it from the core's error
  type if it is public, else compare with what `core.upload` answers in a test) without reading the file; with
  `downscale`, go straight to `convert(.., Some(max_side()))`, which reads the path itself. TIFF/HEIF are never
  read raw; they go to `sips` from the path.
- **R-M2** Downscale after a `too_large` (bytes) refusal of an image whose longest side is already within the
  cap still resamples it up to the cap (`sips -Z 2048` enlarges an 1800 px image). Fix: resample only when the
  longest side exceeds the cap; otherwise re-encode at its size (8-bit PNG via `sips -s format png`), which is
  what gets a 16-bit 1800 px PNG under the byte limit.
- **R-M10** HEIC/TIFF are converted at full resolution first, so a 1.5 MB 12 MP HEIC becomes a 16.8 MB PNG and
  can be refused as over 20 MB, with an error that quotes 20 MB for a 1.5 MB file. Fix: read the dimensions
  first (`sips -g pixelWidth -g pixelHeight <path>`); when either exceeds the cap or the pixel limit and
  `downscale` is set, convert straight to the cap; when not set, answer the core's `too_many_pixels` refusal
  (so the UI offers Downscale) without converting.
- **R-M1** Two files with identical bytes share one entry: the second open selects the first, and Export
  Beside / Show in Finder use the first file's folder. Fix: the desktop's image id is a hash of the core's
  content id and the path (no path: the core's id, as now), 32 lowercase hex digits like the core's (the UI and
  the frontend tests treat ids as opaque strings).

Tests: a sparse file (`File::set_len(3 << 30)`) with a PNG signature is refused `too_large` and the test's
peak RSS stays small (assert the call returns in well under a second instead of measuring memory); an 1800 px
16-bit PNG over the byte limit downscales to 1800 px, not 2048; two identical files in two folders open as two
entries with their own paths; HEIC dimension probe on a generated HEIC (`sips -s format heic` in the test,
skipped when `sips` cannot write HEIC).

### Task A2: Closing, quitting and menu commands that cannot lose work by accident (Rust + UI state)

**Files:** `apps/desktop/src-tauri/src/lib.rs`, `src/menu.rs`, `src/commands.rs`; `frontend/src/App.tsx` (the
menu state it reports), `frontend/src/state/library.ts` (an exported flag), their tests.

- **R-I2** ⌘W, the close button or ⌘Q ends the app at once, discarding every image and trace (a slip from ⌘E
  to ⌘W loses a two-minute Auto). Fix: `MenuState` gains `unexported: u32` — the number of images whose shown
  vector has not been exported or copied since it was traced (the library keeps, per item, the trace key last
  exported/copied; Export All counts for every image it wrote) — and `tracing` is already there. On the main
  window's `CloseRequested`, and for Quit (replace the predefined Quit with our own `e("quit", "Quit
  Studi0Trace", Some("CmdOrCtrl+Q"))` handled in Rust, and also catch `RunEvent::ExitRequested` for the Dock's
  Quit and logout), when `unexported > 0 || tracing`: prevent it and ask with a native dialog (from Rust,
  tauri-plugin-dialog, never blocking the main thread): title `Quit Studi0Trace?`, message `<N> traced
  image(s) have not been exported.` (or `A trace is still running.`), buttons `Quit` / `Cancel`. Quit → exit
  (guard against re-entering the prompt with an `AtomicBool`). Nothing to lose → close as today.
- **R-I3** Holding ⌘⌫ repeats the menu shortcut and removes image after image. Fix: in `on_menu`, a `remove`
  (and `clear`) arriving within 400 ms of the previous one of the same id is dropped (a pure
  `fn debounce(last: Option<Instant>, now: Instant) -> bool`, unit-tested).
- **F-M3** While the Settings window is key, menu commands still act on the main window (⌘⌫ removes the
  selected image, ⌘↩ traces, ⌘E exports). Fix in Rust: in `on_menu`'s forwarding arm, when the `settings`
  window exists and `is_focused()`, do not forward the commands that act on the image (everything except
  `open`, `settings`, `help`, recent items, `quit`, `check-updates`, `acknowledgements`, the View toggles of
  sidebar/inspector are also dropped); a test of the pure classifier `fn acts_on_document(id) -> bool`.
- **R-M12** Open Recent: a click is matched by its index into the list as it is *now*; deleted files stay
  listed; two `logo.png` are indistinguishable. Fix: rebuild the submenu from the list with each item's id
  `recent:<index>` resolved against the list the submenu was built from (keep that list in `Handles`), label
  `name — parent folder name` when two entries share a file name, and skip (and prune on the next rebuild)
  paths that no longer exist; opening a missing recent file removes it from the list and shows the usual
  open error.
- **R-M7** A dropped folder fails with "Is a directory"; a drop with no file paths (from Safari) does nothing
  silently. Fix: a dropped folder opens the image files directly inside it (one level, the extensions in
  `IMAGE_EXTENSIONS`, sorted by name); a drop that yields nothing emits an `open-failures`-style message the UI
  already shows for failed opens (reuse the existing failure path; read `opens.rs` and the UI's handler to
  find it) saying `Nothing to open: drop image files or a folder of them.`
- **R-M9** The app moved while running cannot spawn its worker ("No such file"). Fix: map a spawn error whose
  kind is `NotFound` to the message `Studi0Trace was moved while it was open. Quit and open it again.`

### Task A3: Exports that never overwrite the original or fail on a long name (Rust)

**Files:** `apps/desktop/src-tauri/src/export.rs`, `src/commands.rs`, `src/settings.rs`; tests;
`frontend/src/hooks/useExports.ts` only if the `export_all` answer changes shape (then also `platform/native.ts`
and its types).

- **R-M3** In Ask mode the save panel opens in the original's folder with `logo.png` for a 1× PNG export; Save
  → Replace overwrites the source. Fix: the suggested name for a PNG export is `<stem>.traced.png` when it
  would equal the original's file name, and the write refuses (error `That is the original image; choose
  another name.`) when the chosen path canonicalises to the original's path.
- **R-M4** The temp file `.{name}.studi0trace-tmp` adds 17 bytes, so names of 239–255 bytes fail. Fix: temp
  name `.s0t-<16 hex random or counter+pid>.tmp` in the same folder, created with `create_new`, renamed over
  the target.
- **R-M5** Export All stops at the first failed write and loses which files were written; a retry duplicates
  them. Fix: write all it can and answer `{ written: [paths], failed: [{ name, message }] }`; the UI's toast
  says how many were written and names the failures.
- **R-M6** Export Beside on a read-only volume or a vanished folder fails every time. Fix: when the beside
  write fails with permission denied, read-only filesystem or not found, fall back to the save panel for that
  export (same suggested name).
- **R-M11** One wrong-typed key in `settings.json` resets every setting and the recent list, and the next save
  writes the defaults over the file. Fix: read field by field — start from defaults and take each key that
  deserialises to its type (unknown keys ignored); a test with `{"appearance": 3, "recent": ["/a.png"],
  "liveUpdate": false}` keeps `recent` and `liveUpdate`.

### Task A4: The library keeps Auto, errors belong to their settings (frontend)

**Files:** `frontend/src/state/library.ts`, `frontend/src/components/ParamControl.tsx`, `frontend/src/App.tsx`
(downscale toast), tests (`library` tests, `ParamControl.test.tsx`).

- **F-I1** Leaving a number field without changing it calls `onChange`, and `setParam` always sets
  `preset: null`, so Auto is dropped and ⌘↩ traces without it. Fix: `ParamControl` commits only a changed
  value; `setParam` is a no-op when the value equals the current one.
- **F-M4** Clearing a number field commits 0 (clamped to the minimum). Fix: empty or non-numeric text reverts
  to the current value.
- **F-I2** A trace error never clears when the user switches to settings whose trace is cached (the good trace
  shows under a red "The worker crashed" banner, the sidebar says "Failed"), and a stale job's `fail` writes its
  error onto newer settings. Fix: an error is stored with the trace key it belongs to and shown only while the
  item's current key is that key; `fail` for a job whose key is no longer current records nothing visible.
- **F-M5** Choosing a preset that has a cached trace cancels the running Auto/slow trace for another key. Fix:
  let the running job finish (its answer is cached as any non-current answer is).
- **F-M9** A failed Downscale is silent (unhandled rejection). Fix: catch it and show the error toast the
  other open failures use.

### Task A5: Viewer, inspector and keyboard (frontend)

**Files:** `frontend/src/hooks/useLayerInspector.ts`, `frontend/src/components/Viewer.tsx`,
`frontend/src/components/Inspector.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/lib/shortcuts.ts`,
tests.

- **F-I4** The layer inspector's "drop specks under N px²" resets on every re-trace and every image switch, so
  an export after a live re-trace regains the specks. Fix: inspector state per image id; on a new SVG for the
  same image keep `minArea` and reset only `hidden` and `highlight`.
- **F-M6** The first render after a new SVG applies the previous SVG's hidden indices. Fix: the state records
  the SVG it belongs to and is ignored for any other.
- **F-M1** A tiny image fits above the 32× zoom cap, so Zoom In zooms out. Fix: the fit scale is capped at
  `MAX_SCALE`.
- **F-M2** `isTyping` counts every `INPUT`, including `type=range` and checkboxes, so Remove is ignored after
  touching a slider. Fix: typing means a textarea, a contenteditable, or an input whose type is text-entry
  (`text, search, email, url, tel, password, number`).
- **F-M11** Arrow keys in the sidebar change the selection but do not scroll it into view. Fix:
  `scrollIntoView({ block: "nearest" })` on the selected card when the selection changes by keyboard.
- **F-M13** The split divider lacks `aria-valuemin`/`aria-valuemax` (add 0/100 and `aria-valuenow`); the
  Settings radiogroups get arrow-key navigation with a roving tabindex.

### Task A6: The window follows the Appearance setting (Rust)

**Files:** `apps/desktop/src-tauri/src/settings.rs`, `src/lib.rs`, `src/commands.rs` (the Settings window
creation), tests.

- **F-I3** Appearance only toggles `html.dark`; the native window (vibrancy, title bar, traffic lights, the
  Settings window) keeps following the system, so Dark on a light Mac draws light text on light vibrancy.
  Fix, from Rust only (no new webview permission): `fn theme_for(appearance: &str) -> Option<tauri::Theme>`
  (`"light"` → Light, `"dark"` → Dark, anything else → None = follow the system), applied with
  `set_theme` to every webview window at setup, whenever settings are saved, and to the Settings window when it
  is created. Verify natively: screenshots of both windows with the Mac in its current appearance and the
  setting on the opposite one, and on System.

---

## Part B — Release and retirement

### Task B1: One version

**Files:**
- Create: `apps/desktop/scripts/version.sh`
- Modify: `apps/desktop/src-tauri/src/lib.rs` (test module) or create `apps/desktop/src-tauri/tests/version.rs`

**Interfaces:** Produces `bash apps/desktop/scripts/version.sh X.Y.Z`, used by the README's release steps (Task B7).

- [ ] **Step 1: Write the failing test** `apps/desktop/src-tauri/tests/version.rs`: reads `../tauri.conf.json` and `../package.json` relative to `env!("CARGO_MANIFEST_DIR")`, parses each with `serde_json`, and asserts both `"version"` fields equal `env!("CARGO_PKG_VERSION")`. It passes today (all 0.3.0); make it fail first by checking it against a deliberately wrong literal, then restore.
- [ ] **Step 2: Write `version.sh`**: `set -euo pipefail`; requires one argument matching `^[0-9]+\.[0-9]+\.[0-9]+$` (else prints usage to stderr, exit 2); rewrites the `"version"` of `apps/desktop/package.json` and `apps/desktop/src-tauri/tauri.conf.json` (use `node -e` with JSON parse/stringify at 2-space indent plus trailing newline, so formatting is stable), and the `version = "…"` line in the `[package]` table of `apps/desktop/src-tauri/Cargo.toml` (the first `version =` line only); then runs `cargo update -p studi0trace-desktop --offline` from the repo root so `Cargo.lock` follows; prints the three files' new versions. Works from any cwd (`cd "$(dirname "$0")/../../.."`).
- [ ] **Step 3: Verify**: run `bash apps/desktop/scripts/version.sh 0.3.1`, run the test (passes), `git diff --stat` shows the 4 files; run `bash apps/desktop/scripts/version.sh 0.3.0` and `git diff` is empty. Run with `abc` → exit 2.
- [ ] **Step 4: Commit** `desktop: one version, held equal across package.json, Cargo.toml and tauri.conf.json; version.sh sets it`.

### Task B2: Universal, ad-hoc signed bundle

**Files:**
- Modify: `apps/desktop/src-tauri/tauri.conf.json` (`bundle.macOS.signingIdentity: "-"`)
- Modify: `apps/desktop/package.json` (script `"build:universal": "tauri build --target universal-apple-darwin"`)
- Modify: `apps/desktop/scripts/smoke.sh` only if it hard-codes `target/release/bundle` and must also accept `target/universal-apple-darwin/release/bundle` (add an optional `SMOKE_APP` env override; default unchanged).

- [ ] **Step 1**: `rustup target add x86_64-apple-darwin`.
- [ ] **Step 2**: set `signingIdentity` to `"-"`.
- [ ] **Step 3**: `cd frontend && npm run build`, then `cd apps/desktop && CI=true npm run build:universal`. Expected artifacts: `target/universal-apple-darwin/release/bundle/macos/Studi0Trace.app` and `…/dmg/Studi0Trace_0.3.0_universal.dmg`.
- [ ] **Step 4: Verify**: `lipo -archs target/universal-apple-darwin/release/bundle/macos/Studi0Trace.app/Contents/MacOS/studi0trace-desktop` prints `x86_64 arm64`; `codesign -dv --verbose=2 <app> 2>&1` shows `Signature=adhoc`; `codesign --verify --deep --strict <app>` exits 0; `SMOKE_APP=<that app> npm run smoke` (or the script's equivalent) passes: the worker runs from the universal binary. Also run `arch -x86_64 <app>/Contents/MacOS/studi0trace-desktop --trace-worker < /dev/null; echo $?` only if Rosetta is installed (`/usr/bin/pgrep oahd` or `arch -x86_64 /usr/bin/true`); a non-zero exit with a JSON error line on stdout is the expected answer to empty input — the point is that the x86_64 slice starts. Skip with a note if Rosetta is absent.
- [ ] **Step 5: Commit** `desktop: ad-hoc signed, universal (arm64 + x86_64) bundle`.

### Task B3: The updater

**Files:**
- Modify: `apps/desktop/src-tauri/Cargo.toml` (add `tauri-plugin-updater = "2"`, the newest 2.x that resolves with tauri 2.12; never a 3.0 alpha)
- Modify: `apps/desktop/src-tauri/tauri.conf.json` (`plugins.updater`)
- Create: `apps/desktop/src-tauri/tauri.release.conf.json` (`{ "bundle": { "createUpdaterArtifacts": true } }`)
- Create: `apps/desktop/src-tauri/src/updates.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs` (plugin, launch check), `src/menu.rs` (item `check-updates`), `src/settings.rs` (`check_for_updates`)
- Modify: `frontend/src/platform/types.ts` (`checkForUpdates: boolean`, default `true`), `frontend/src/components/SettingsView.tsx` (a toggle), their tests; `apps/desktop/src-tauri/src/commands.rs` Settings window height if the new row would make it scroll.

**Behaviour:**
- `tauri.conf.json`: `"plugins": { "updater": { "pubkey": "<the Global Constraints key>", "endpoints": ["https://github.com/timothynice/tracer/releases/latest/download/latest.json"] } }`.
- Settings gains `check_for_updates: bool` (`checkForUpdates` in JSON), default `true`; a file without the key reads as `true`.
- Menu: `e("check-updates", "Check for Updates…", None)` in the Studi0Trace menu right after About (separator after it as today). Handled in Rust (`menu::on_menu` → `updates::check(app, Manual)`), never sent to the UI.
- At launch (in `setup`, spawned with `tauri::async_runtime::spawn`, after the window exists), if `settings::load(app).check_for_updates` is true: `updates::check(app, Automatic)`.
- `updates::check(app, how)`: `app.updater()?.check().await`.
  - `Ok(Some(update))` → a native message dialog (tauri-plugin-dialog `MessageDialogBuilder` from Rust, `MessageDialogButtons::OkCancelCustom("Install and Relaunch", "Later")`), title `Studi0Trace <new version> is available`, message: `You have <current version>.` plus, when `update.body` is non-empty, a blank line and the body trimmed to at most 600 characters (cut at a char boundary, `…` appended). On Install: `update.download_and_install(|_, _| {}, || {}).await`, then `app.restart()`; on an install error, an error dialog `The update could not be installed.` with the error text.
  - `Ok(None)` → Manual: info dialog `Studi0Trace is up to date` / `You have the newest version, <current>.`; Automatic: nothing.
  - `Err(e)` → Manual: dialog `Could not check for updates` with `e`; Automatic: nothing (log to stderr with `eprintln!`).
  - Only one check at a time: a static `AtomicBool` `CHECKING`; a second request while one runs is dropped (a Manual one while an Automatic one runs still gets its dialog when the first finishes — simplest acceptable rule: the running check becomes Manual by setting a second `AtomicBool` `SHOW_RESULT`). Release the flag on every path.
- Keep the pure parts testable: `fn release_notes(current: &str, body: Option<&str>) -> String` and `enum How { Manual, Automatic }` with `fn quiet(how, outcome) -> bool` are unit-tested in `updates.rs`.

- [ ] **Step 1: Failing tests**: settings default/old-file test gains `checkForUpdates: true`; `updates.rs` tests for `release_notes` (empty body, long body trimmed to 600 chars + `…`, multibyte boundary) and `quiet` (Automatic+UpToDate quiet, Automatic+Error quiet, Manual+anything not quiet, Available never quiet); `menu.rs`'s existing id/label tests include `check-updates`; frontend `SettingsView.test.tsx` checks the toggle saves `checkForUpdates: false`.
- [ ] **Step 2: Implement** as above. Add the plugin with `.plugin(tauri_plugin_updater::Builder::new().build())`.
- [ ] **Step 3: Verify**: all tests; clippy; `cd frontend && npm run build`; `cd apps/desktop && CI=true npm run build` still succeeds **without** `TAURI_SIGNING_PRIVATE_KEY` set (no updater artifacts locally). Then run the built app: Studi0Trace ▸ Check for Updates… shows "Could not check for updates" (no release exists yet, so GitHub answers 404) — that is the expected manual result today; take a window screenshot with `apps/desktop/scripts/shot.swift` if the dialog is capturable. Then verify the release config: `TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/studi0trace.key)" TAURI_SIGNING_PRIVATE_KEY_PASSWORD="" CI=true npx tauri build --config src-tauri/tauri.release.conf.json` (from `apps/desktop`) produces `bundle/macos/Studi0Trace.app.tar.gz` and `.tar.gz.sig`. Read the key only into that environment variable for that one command; never echo it, never write it anywhere.
- [ ] **Step 4: Commit** `desktop: Check for Updates…, a quiet check at launch (a setting), updates signed with the app's own minisign key`.

### Task B4: Licence notices

**Files:**
- Create: `apps/desktop/about.toml`, `apps/desktop/about.hbs`, `apps/desktop/scripts/notices.sh`, `apps/desktop/scripts/npm-notices.mjs`
- Create (generated, committed): `apps/desktop/src-tauri/resources/THIRD_PARTY_NOTICES.html`
- Modify: `tauri.conf.json` (`bundle.resources`), `src/menu.rs` (Help ▸ `acknowledgements`), README's build section (Task B7 links to it)

**Behaviour:**
- `cargo install cargo-about --locked --version 0.9.2` (developer machine and CI).
- `about.toml`: `accepted = [...]` with every licence the tree actually needs (start with MIT, Apache-2.0, Apache-2.0 WITH LLVM-exception, BSD-2-Clause, BSD-3-Clause, ISC, Zlib, Unicode-3.0, Unicode-DFS-2016, MPL-2.0, CC0-1.0, BSL-1.0, OFL-1.1 and add only what `cargo about` reports missing); `targets = ["aarch64-apple-darwin", "x86_64-apple-darwin"]`; `ignore-build-dependencies = true`, `ignore-dev-dependencies = true`.
- `notices.sh`: from the repo root, `cargo about generate --manifest-path apps/desktop/src-tauri/Cargo.toml -c apps/desktop/about.toml apps/desktop/about.hbs` to a temp file, then `node apps/desktop/scripts/npm-notices.mjs` appends the frontend's production npm packages (walk `frontend/package.json` `dependencies` transitively through `frontend/node_modules/*/package.json` `dependencies`; for each package: name, version, licence field, and the text of its `LICENSE*`/`LICENCE*`/`COPYING*` file if present) and the bundled fonts (Poppins, SIL OFL 1.1 — find its licence file in the repo; if none is present, include the OFL 1.1 text from `node_modules` if the font comes from an npm package, else name the licence and its URL), writing one self-contained HTML file (inline CSS, light/dark via `prefers-color-scheme`, system font) to `apps/desktop/src-tauri/resources/THIRD_PARTY_NOTICES.html`. Deterministic: sorted by name then version, no timestamps, so a re-run with the same lockfiles gives a byte-identical file.
- `tauri.conf.json`: `"resources": { "resources/THIRD_PARTY_NOTICES.html": "THIRD_PARTY_NOTICES.html" }`.
- Help ▸ `e("acknowledgements", "Acknowledgements", None)` after Studi0Trace Help. Rust resolves `app.path().resolve("THIRD_PARTY_NOTICES.html", BaseDirectory::Resource)` and opens it with `tauri_plugin_opener::OpenerExt::opener().open_path(path, None::<&str>)`; a failure is an error dialog.

- [ ] **Step 1: Failing test**: `apps/desktop/src-tauri/tests/notices.rs` reads the committed HTML and asserts it contains `tauri`, `serde`, `react`, `react-dom`, `Poppins` and the string `MIT License`, and contains no absolute path under `/Users/`.
- [ ] **Step 2: Implement** and run `bash apps/desktop/scripts/notices.sh`; run it twice and `git diff --exit-code` on the HTML after the second run.
- [ ] **Step 3: Verify**: tests, clippy, `CI=true npm run build`, then `ls <app>/Contents/Resources/THIRD_PARTY_NOTICES.html`; launch the built app and choose Help ▸ Acknowledgements (via `osascript` System Events if allowed) — the file opens in the default browser.
- [ ] **Step 4: Commit** `desktop: third-party notices (cargo about + the frontend's npm tree), bundled and shown from Help ▸ Acknowledgements`.

### Task B5: CI and release workflows

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/release.yml`

**ci.yml** — `on: push (branches: [main]) and pull_request`; `concurrency: ci-${{ github.ref }}` with cancel-in-progress; one job on `macos-15`:
1. `actions/checkout@v4`
2. `actions/setup-node@v4` with `node-version` = the major in `node --version` on the developer machine (26 at the time of writing; the test setup shims Node 26's localStorage), `cache: npm`, `cache-dependency-path: frontend/package-lock.json`
3. `dtolnay/rust-toolchain@stable`, `Swatinem/rust-cache@v2`
4. `npm ci`, `npm run test:run`, `npm run build` in `frontend`
5. `cargo test --workspace --release`
6. `cargo clippy -p studi0trace-desktop --release --no-deps -- -D warnings`

**release.yml** — `on: push: tags: ['v*']` and `workflow_dispatch`; `permissions: contents: write`; one job on `macos-15`:
1. checkout; setup-node as above with both lockfiles (`frontend/package-lock.json`, `apps/desktop/package-lock.json`) as `cache-dependency-path`
2. `dtolnay/rust-toolchain@stable` with `targets: aarch64-apple-darwin,x86_64-apple-darwin`; rust-cache
3. When triggered by a tag: fail unless `${GITHUB_REF_NAME#v}` equals `.version` of `apps/desktop/src-tauri/tauri.conf.json` (print both)
4. `npm ci` in `frontend` and in `apps/desktop`
5. `cargo install cargo-about --locked --version 0.9.2`; `bash apps/desktop/scripts/notices.sh`
6. `tauri-apps/tauri-action@v0` with env `GITHUB_TOKEN`, `TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}`, and with `projectPath: apps/desktop`, `tagName: v__VERSION__`, `releaseName: Studi0Trace __VERSION__`, `releaseDraft: true`, `prerelease: false`, `includeUpdaterJson: true`, `args: --target universal-apple-darwin --config src-tauri/tauri.release.conf.json`, and a `releaseBody` that says: download the `.dmg`, drag Studi0Trace to Applications, and on first open use System Settings ▸ Privacy & Security ▸ Open Anyway (or `xattr -dr com.apple.quarantine /Applications/Studi0Trace.app`), because the app is not notarized.

- [ ] **Step 1**: write both files.
- [ ] **Step 2: Verify**: lint both with actionlint — `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest -color` (Docker Desktop may need `open -a Docker` and a wait); zero findings. Also check that the cargo/npm commands in ci.yml are the ones that pass locally (they are the Global Constraints' commands).
- [ ] **Step 3: Commit** `ci: tests on every push and pull request; a draft release with the universal app, dmg and updater manifest on a v* tag`.

### Task B6: Retire the deploy and the extra engines

**Files:**
- Delete (`git rm`): `render.yaml`, `backend/Dockerfile`, `backend/vexel-rs/Cargo.lock`, `backend/tools/sync_vexel_lock.py`, `backend/tests/test_vexel_lock_matches_workspace.py`, and any `.dockerignore` for the backend
- Modify: root `Cargo.toml` (drop the "MIRRORED in backend/Dockerfile" comment block; keep the profile)
- Move: `backend/studi0trace/engines/potrace.py`, `vtracer.py` → `backend/bench/engines/potrace.py`, `vtracer.py` (with `backend/bench/engines/__init__.py`); they still register themselves into `studi0trace.engines.registry` when imported
- Modify: `backend/studi0trace/engines/registry.py` `load_builtin()` imports Vexel only; `backend/bench/adapters/__init__.py` (and `bench/cli.py`/`headtohead.py` if they need it) imports `bench.engines` before it looks engines up; `backend/pyproject.toml` moves `vtracer` from `dependencies` to a `bench` optional extra (and wherever the dev setup installs extras, include it)
- Modify: tests — `test_engines.py`'s Potrace/VTracer tests import from `bench.engines`; `test_api.py` no longer expects them in `GET /engines`; `test_engines.py:74`'s registry assertion follows the new rule (`registry.ids()` after `load_builtin()` is `["vexel"]`; after `import bench.engines` it includes potrace and vtracer)
- Modify: `backend/studi0trace/settings.py` if it holds deploy-only settings that nothing reads any more (leave the dev server's settings alone)

- [ ] **Step 1**: run the Python suite to record the baseline (pass/skip counts).
- [ ] **Step 2**: make the changes. The FastAPI app stays (spec: kept as a developer tool).
- [ ] **Step 3: Verify**: `cd backend && .venv/bin/python -m pytest -q` passes with the baseline count minus exactly the removed lock tests; `.venv/bin/python -m bench --help` works; `.venv/bin/python -c "import bench.adapters"`; `grep -rn "Dockerfile\|render.yaml\|sync_vexel_lock" --exclude-dir=node_modules --exclude-dir=target --exclude-dir=.git .` lists only docs that Task B7 rewrites (list them in the report). `cargo test --workspace --release` still passes.
- [ ] **Step 4: Commit** `retire: the Render deploy, the Docker image and its lock; Potrace and VTracer leave the server for the bench`.

### Task B7: Docs for the app

**Files:** `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-09-24-studi0trace-local-app-design.md` (status line), `apps/desktop/README.md` if present.

- README, in this order: title + one-line pitch; a screenshot (take one of the built app with `apps/desktop/scripts/shot.swift` on a sample traced in Split view, save as `docs/assets/studi0trace-mac.png`, under 400 KB — `sips -Z 1600` if needed); **Download** (Releases link `https://github.com/timothynice/tracer/releases/latest`, macOS 13+, Apple silicon and Intel); **First open** (not notarized: Open Anyway in System Settings ▸ Privacy & Security, or the `xattr -dr com.apple.quarantine /Applications/Studi0Trace.app` one-liner; move it to Applications before first launch so updates can replace it); **What it does** (short feature list from the app: presets and Auto, split/side/overlay/vector views, layer inspector, export SVG/PNG, Open With from Finder, HEIC/TIFF, updates); **Build from source** (prereqs, `cd frontend && npm ci && npm run build`, `cd apps/desktop && npm ci && CI=true npm run build`, the universal build, `npm run dev`); **Releasing** (version.sh, tag `vX.Y.Z`, push the tag, the draft release, publish it; the one-time secret setup: `gh secret set TAURI_SIGNING_PRIVATE_KEY < ~/.tauri/studi0trace.key` — the key is the maintainer's, keep a backup, losing it means existing installs cannot verify updates; notices regenerate in CI); then the existing Vexel description and developer sections (engine, bench, Python reference, the dev server as a harness), with the Deploy section, the "Pointing a domain" and "Free tier" sections removed, and the API section labelled as the development server.
- CONTRIBUTING: setup for the app first (Rust 1.90+, Node, `npm ci` in both), the test commands of the Global Constraints, then the existing Python/engine rules; remove Docker/lock-sync instructions.
- CLAUDE.md: drop the Docker/lock bullet text that no longer applies (the `Python env` bullet's Docker sentences), note `version.sh`, the updater (Rust-only, key location, release config), `notices.sh`, the workflows, and that the FastAPI app is a developer tool; keep everything about the engine untouched.
- Roadmap spec: under the 2026-10-02 line add `**2026-10-03:** plan 4 is \`2026-10-03-studi0trace-release-design.md\`; the FastAPI app stays as a developer tool (it is what the core's api and auto fixtures are exported from).`

- [ ] **Step 1**: write; **Step 2: Verify**: every command in the README's Build and Releasing sections was run in this plan (B1–B5) or is run now; every relative link resolves (`grep -o '](\([^)#]*\)' README.md CONTRIBUTING.md` and check each path exists); **Step 3: Commit** `docs: the README is the app's download page; contributing and CLAUDE.md follow the release and the retired deploy`.

### Task B8: Final verification

- [ ] Full suites (frontend, cargo workspace, Python), clippy, `CI=true npm run build` and `npm run smoke`, the universal build's `lipo`/`codesign` checks, and a native pass over the bug-hunt checklist (`scratchpad/qa-native/report.md`'s checklist) on the built app with screenshots in light and dark.
