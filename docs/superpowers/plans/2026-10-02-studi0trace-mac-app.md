# Studi0Trace for Mac Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Mac app, `Studi0Trace.app`, that opens images from Finder, traces them with the Rust core in a cancellable worker process, and exports SVG and PNG, with the web UI reshaped into a three-pane Mac window after Tim's concept.

**Architecture:** A Tauri 2 shell (`apps/desktop/src-tauri`, a member of the Cargo workspace) holds `studi0trace_core::api::Core` for describing, presets and intake, keeps the open images' bytes, and runs every trace in a child process (its own binary with `--trace-worker`), one at a time, killed to cancel. The React app in `frontend/` talks to it through a `platform` layer (native: `invoke` and events; web: the existing HTTP client, as a development harness), keeps its images in a framework-free library store, and lays them out as title bar, sidebar, viewer and Vectorize panel.

**Tech Stack:** Rust 1.90+ (local 1.98), Tauri 2.12 with tauri-plugin-dialog 2.8, -store 2.5, -window-state 2.5, -clipboard-manager 2.4, -opener 2.7; `@tauri-apps/cli` and `@tauri-apps/api` 2.12; React 18, TypeScript 5.8, Tailwind 3, Vite 6, Vitest 3; macOS `sips` for HEIC/HEIF/TIFF and downscaling.

**Spec:** `docs/superpowers/specs/2026-10-02-studi0trace-mac-app-design.md`. **Concept:** `docs/superpowers/specs/assets/2026-10-02-mac-app-concept.webp` (open it before any UI task).

## Global Constraints

- macOS 13 or later, Apple silicon only: `"minimumSystemVersion": "13.0"`, target `aarch64-apple-darwin`. No signing, no notarisation, no Developer ID, ever (`bundle.macOS.signingIdentity` stays unset; the linker's ad-hoc signature is enough).
- Tauri **2** stable: `tauri = "2.12"`, `tauri-build = "2.7"`, the plugins at the versions above, `@tauri-apps/cli`/`@tauri-apps/api` `^2.12`. Never a `3.0.0-alpha`.
- Product name **Studi0Trace** (with the zero), identifier `com.studi0.trace`, version `0.3.0`, subtitle "Turn images into clean vectors". The concept's "StudioTrace" spelling is not used.
- The engine (`backend/vexel-rs`) and the core (`crates/studi0trace-core`) do not change in this plan. The app uses only `studi0trace_core::api::{Core, ApiError}` and what they return.
- `cargo test --workspace --release` stays green at every commit (the core's fixtures are compared to the bit; if a new dependency's features change any core result, stop and report it). Never `--all-features`.
- The desktop crate embeds `frontend/dist` when it compiles (`tauri::generate_context!`), so any cargo build or test of the workspace needs it: `cd frontend && npm run build` first on a fresh checkout, and again before a release build (`npm run build` in `apps/desktop` does it through `beforeBuildCommand`).
- `cd backend && .venv/bin/python -m pytest tests/test_vexel_lock_matches_workspace.py` stays green; when the workspace's lock moves a dependency vexel-rs shares, run `cd backend && .venv/bin/python -m tools.sync_vexel_lock` and commit `backend/vexel-rs/Cargo.lock` with it.
- `cd frontend && npm run test:run` and `npm run build` stay green from Task 6 on.
- The webview never names a path to write. Rust shows the save and folder panels, picks the names and writes. The webview may name paths only to open them (paths it was given by Rust: drops, Open With, recent files, the open panel).
- Every error the UI shows carries a `code` (the core's codes, plus `cancelled`, `engine_crashed`, `conversion_failed`, `io_error`), as `ApiError(code, message, status, detail)` in the UI.
- Never a one-sided coloured border as a highlight (no `border-l-2 border-…`): use a dot, an all-round ring, a tint or a weight change (CLAUDE.md).
- Copy: buttons and menu items in title case ("Generate Vector", "Export SVG…", "Show in Finder"); descriptions and messages in sentence case. An item that opens a panel ends in an ellipsis.
- Shell: never `rm -rf` (use `trash` or `rm` on named files); never a bare `git stash`. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## The look (every UI task)

Open the concept image first. What is kept and what changes:

| | value |
|---|---|
| Interface font | `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif`; 13 px / 18 px body, 11 px / 14 px secondary, 13 px semibold headings. Tailwind `font-sans` becomes this stack. |
| Wordmark | Poppins 600, 15 px, only in the title bar ("Studi0Trace") and the About panel; `font-brand` utility. |
| Colours | The existing tokens in `frontend/src/styles.css` stay. Added: `--window` (the window behind the panels; light `220 14% 93%`, dark `216 22% 9%`) and `--mac-accent`, which is the CSS system colour `AccentColor` where the webview supports it and `211 100% 52%` (macOS blue) where it does not (see Task 8). Selection, focus rings, the radio dot and the primary button use `--mac-accent`; everything else uses the brand tokens. |
| Panels | Three rounded cards (`rounded-xl`, 12 px) on `--window`, 8 px from the window edges and 8 px apart, `bg-card` with a 1 px `border` (the sidebar card is translucent over the vibrancy: `bg-card/60` in light, `bg-card/45` in dark). |
| Density | Title bar 52 px. Toolbar and icon buttons 28 × 28 px, `rounded-md`. Segmented controls 28 px. List rows and preset cards: 10 px padding, `rounded-lg`. Primary button 36 px, full width, `rounded-lg`, accent background, white 13 px semibold text. Secondary buttons 32 px. |
| Selection | Selected image card: `ring-2 ring-[hsl(var(--mac-accent))]` all round. Selected preset card: `bg-[hsl(var(--mac-accent)/0.10)] ring-1 ring-[hsl(var(--mac-accent)/0.6)]` and a filled radio. |
| Status | A 6 px dot (`h-1.5 w-1.5 rounded-full`): accent while tracing (pulsing), `bg-warning` queued, `bg-destructive` failed, nothing when idle. The brand's yellow `dot-brand` marks Auto's pick. |
| Behaviour | `user-select: none` and `cursor: default` on everything but text fields and the SVG source view; no page scroll (`overscroll-behavior: none`), no web context menu (`contextmenu` prevented outside text fields), motion ≤ 150 ms and none under `prefers-reduced-motion`. Focus rings: `outline: 3px solid hsl(var(--mac-accent) / 0.5); outline-offset: 1px` on `:focus-visible`. |

**Visual verification.** This machine's Claude app cannot screenshot windows (`screencapture` fails without Screen Recording permission), so UI tasks are checked in the built-in browser: start `.claude/launch.json`'s `backend` and `frontend` (`preview_start` with each name), open `http://localhost:5173`, and use `read_page` for structure and `computer` `screenshot` for the look, in light and dark (`resize_window` with `colorScheme`), at 1280 × 800 and 960 × 640. Compare with the concept. If an implementer subagent has no browser tools, the controller does this check after the task's review. The native window (traffic lights, vibrancy, menus) is checked by running `npm run dev` in `apps/desktop` and, where screenshots are impossible, by the window's own checks in Task 14's smoke script; anything only a person can judge is listed in the final report for Tim.

## File structure

```
Cargo.toml                                   + "apps/desktop/src-tauri" in members; [profile.dev.package.*] opt-level 3 for the engine
apps/desktop/
  package.json                               @tauri-apps/cli; scripts dev, build, icon
  scripts/make-icon.py                        icons/icon.svg → icons/icon-1024.png (resvg-py, the backend venv)
  scripts/smoke.sh                            build, open a sample with the app, check it answered
  src-tauri/
    Cargo.toml, build.rs, tauri.conf.json, capabilities/default.json
    icons/icon.svg                            the app icon's source (squircle, the trace mark)
    icons/*                                   generated by `tauri icon` (committed)
    src/main.rs                               --trace-worker → worker::main(); else run()
    src/lib.rs                                run(): plugins, AppState, commands, menu, window and run events
    src/error.rs                              CommandError {status, body}; from ApiError; the app's own codes
    src/store.rs                              Images: id → OpenImage (bytes, name, path, size, format)
    src/intake.rs                             open a path or bytes: sniff, sips-convert, downscale, Core::upload
    src/worker.rs                             the child: one job from stdin → Core::vectorize → one JSON line
    src/queue.rs                              the parent: one worker at a time, supersede, cancel, crash
    src/export.rs                             unique names, save/folder panels, write, reveal
    src/menu.rs                               the menu bar, its ids, menu state, Open Recent
    src/settings.rs                           settings.json via tauri-plugin-store; broadcast
    src/commands.rs                           every #[tauri::command]
    tests/worker.rs                           the real binary: protocol, cancel, supersede, crash
frontend/src/
  platform/types.ts                           Platform, OpenedImage, OpenOutcome, Settings, MenuCommand, MenuState
  platform/web.ts                             the HTTP client and browser fallbacks
  platform/native.ts                          invoke + listen
  platform/index.ts                           `platform`, chosen once
  state/library.ts                            createLibrary(): images, selection, settings, traces, jobs, live rule
  state/useLibrary.ts                         useSyncExternalStore over it
  components/TitleBar.tsx, AppShell.tsx, Sidebar.tsx, ImageCard.tsx, EmptyState.tsx,
             Viewer.tsx (from Canvas.tsx), ViewerToolbar.tsx, VectorizePanel.tsx, PresetCards.tsx,
             TraceButton.tsx, ExportMenu.tsx, SettingsView.tsx
  App.tsx                                     composes them; menu commands; window-level drop
  (removed) components/Header, StatusPill, ThemeToggle, Dropzone, Actions, EngineTabs, Canvas (→ Viewer),
            Presets (→ PresetCards), hooks/useUpload, useVectorize, useParams, useHealth, with their tests
```

---

### Task 1: The Tauri shell in the workspace

An empty Studi0Trace window around the existing UI, built from the workspace, with its icon. Nothing traces yet.

**Files:**
- Modify: `Cargo.toml` (root)
- Create: `apps/desktop/package.json`, `apps/desktop/.gitignore`, `apps/desktop/scripts/make-icon.py`
- Create: `apps/desktop/src-tauri/Cargo.toml`, `build.rs`, `tauri.conf.json`, `capabilities/default.json`, `icons/icon.svg`, `src/main.rs`, `src/lib.rs`, `src/worker.rs` (stub)
- Generated and committed: `apps/desktop/src-tauri/icons/{32x32.png,128x128.png,128x128@2x.png,icon.icns,icon.png,icon-1024.png}`, `apps/desktop/package-lock.json`, `Cargo.lock`

**Interfaces:**
- Produces: the crate `studi0trace-desktop` (lib `studi0trace_desktop`, bin `studi0trace-desktop`) with `pub fn run()` and `pub mod worker { pub const FLAG: &str = "--trace-worker"; pub fn main() -> i32 }`; the window label `"main"`; `npm run dev` / `npm run build` in `apps/desktop`.

- [ ] **Step 1: Add the crate to the workspace**

In the root `Cargo.toml`, change the members line and add the dev-profile block after `[profile.release]`:

```toml
members = ["backend/vexel-rs", "crates/studi0trace-core", "apps/desktop/src-tauri"]
```

```toml
# `tauri dev` builds the app in the dev profile. The engine, the core and the image and render crates under them are
# optimised there as well, or a trace that takes a second in release takes a minute in development. (It also makes
# `cargo test` without --release run them optimised; the core's fixtures hold either way.)
[profile.dev.package.vexel-rs]
opt-level = 3
[profile.dev.package.studi0trace-core]
opt-level = 3
[profile.dev.package.resvg]
opt-level = 3
[profile.dev.package.tiny-skia]
opt-level = 3
[profile.dev.package.image]
opt-level = 3
[profile.dev.package.zune-jpeg]
opt-level = 3
[profile.dev.package.png]
opt-level = 3
```

- [ ] **Step 2: Write the crate**

`apps/desktop/src-tauri/Cargo.toml`:

```toml
[package]
name = "studi0trace-desktop"
version = "0.3.0"
edition = "2021"
rust-version = "1.90"
license = "MIT"
description = "Studi0Trace for Mac: the Tauri shell around studi0trace-core"
publish = false

[lib]
name = "studi0trace_desktop"
crate-type = ["rlib"]

[[bin]]
name = "studi0trace-desktop"
path = "src/main.rs"

[build-dependencies]
tauri-build = { version = "2.7", features = [] }

[dependencies]
# no `macos-private-api`: transparent windows no longer need it (tauri 2.12), and tauri-build insists the feature
# and `app.macOSPrivateApi` agree, so neither is set
tauri = { version = "2.12", features = [] }
tauri-plugin-dialog = "2.8"
tauri-plugin-store = "2.5"
tauri-plugin-window-state = "2.5"
tauri-plugin-clipboard-manager = "2.4"
tauri-plugin-opener = "2.7"
studi0trace-core = { path = "../../../crates/studi0trace-core" }
serde = { version = "1", features = ["derive"] }
serde_json = { version = "1", features = ["preserve_order", "float_roundtrip"] }

[dev-dependencies]
image = { version = "0.25", default-features = false, features = ["png"] }
```

`apps/desktop/src-tauri/build.rs`:

```rust
fn main() {
    tauri_build::build()
}
```

`apps/desktop/src-tauri/src/main.rs`:

```rust
//! One binary, two roles: the app, and (with `--trace-worker`) the child process a trace runs in. The worker
//! branch comes before anything of Tauri's or AppKit's is touched, so a worker never shows in the Dock.
fn main() {
    if std::env::args().nth(1).as_deref() == Some(studi0trace_desktop::worker::FLAG) {
        std::process::exit(studi0trace_desktop::worker::main());
    }
    studi0trace_desktop::run();
}
```

`apps/desktop/src-tauri/src/worker.rs` (Task 3 fills it in):

```rust
//! The trace worker (Task 3).
pub const FLAG: &str = "--trace-worker";

pub fn main() -> i32 {
    2
}
```

`apps/desktop/src-tauri/src/lib.rs`:

```rust
//! Studi0Trace for Mac: the Tauri shell around `studi0trace_core::api::Core`.
pub mod worker;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("Studi0Trace failed to start");
}
```

- [ ] **Step 3: Write the configuration**

`apps/desktop/src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Studi0Trace",
  "version": "0.3.0",
  "identifier": "com.studi0.trace",
  "build": {
    "beforeDevCommand": "npm run web:dev",
    "devUrl": "http://localhost:5173",
    "beforeBuildCommand": "npm run web:build",
    "frontendDist": "../../../frontend/dist"
  },
  "app": {
    "windows": [
      {
        "label": "main",
        "title": "Studi0Trace",
        "width": 1280,
        "height": 800,
        "minWidth": 960,
        "minHeight": 640,
        "titleBarStyle": "Overlay",
        "hiddenTitle": true,
        "trafficLightPosition": { "x": 18, "y": 20 },
        "transparent": true,
        "windowEffects": { "effects": ["sidebar"], "state": "followsWindowActiveState" },
        "dragDropEnabled": true,
        "zoomHotkeysEnabled": false
      }
    ],
    "security": {
      "csp": "default-src 'self' ipc: http://ipc.localhost; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:",
      "devCsp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": ["app", "dmg"],
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png", "icons/icon.icns", "icons/icon.png"],
    "category": "public.app-category.graphics-design",
    "shortDescription": "Turn images into clean vectors",
    "copyright": "Copyright © 2026 Timothy Nice. MIT licensed.",
    "macOS": { "minimumSystemVersion": "13.0" }
  }
}
```

`apps/desktop/src-tauri/capabilities/default.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "The main window and the settings window",
  "windows": ["main", "settings"],
  "permissions": [
    "core:default",
    "core:window:allow-start-dragging",
    "dialog:default",
    "store:default",
    "window-state:default",
    "clipboard-manager:allow-write-text",
    "opener:allow-reveal-item-in-dir"
  ]
}
```

`apps/desktop/package.json`:

```json
{
  "name": "studi0trace-desktop",
  "private": true,
  "version": "0.3.0",
  "type": "module",
  "scripts": {
    "tauri": "tauri",
    "dev": "tauri dev",
    "build": "tauri build",
    "web:dev": "npm --prefix ../../frontend run dev -- --port 5173 --strictPort",
    "web:build": "npm --prefix ../../frontend run build",
    "icon": "../../backend/.venv/bin/python scripts/make-icon.py"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2.12.1"
  }
}
```

`apps/desktop/.gitignore`:

```
node_modules/
src-tauri/gen/
```

- [ ] **Step 4: Draw the icon and generate its sizes**

`apps/desktop/src-tauri/icons/icon.svg` (the macOS grid: an 824 px squircle on a 1024 canvas, the mark's trace in the brand yellow):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2E3A4B"/>
      <stop offset="1" stop-color="#151B24"/>
    </linearGradient>
    <filter id="drop" x="-10%" y="-10%" width="120%" height="130%">
      <feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#000000" flood-opacity="0.32"/>
    </filter>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="185" fill="url(#body)" filter="url(#drop)"/>
  <rect x="100.5" y="100.5" width="823" height="823" rx="184.5" fill="none" stroke="#FFFFFF" stroke-opacity="0.08"/>
  <g transform="translate(512 512) scale(3.2) translate(-131 -118)">
    <path d="M66 160c18-56 40-84 62-84 26 0 22 60 44 60 16 0 24-22 24-46" fill="none" stroke="#FFFA7A" stroke-width="20" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="66" cy="160" r="12" fill="#FFFA7A"/>
  </g>
</svg>
```

`apps/desktop/scripts/make-icon.py`:

```python
"""Render src-tauri/icons/icon.svg to the 1024 px PNG `tauri icon` makes the app's icons from.

Run from apps/desktop with the backend's venv (it has resvg-py): `npm run icon`, then
`npx tauri icon src-tauri/icons/icon-1024.png --output <tmp>` and copy the five files tauri.conf.json lists.
"""
from pathlib import Path

import resvg_py

here = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"
png = resvg_py.svg_to_bytes(svg_string=(here / "icon.svg").read_text(encoding="utf-8"), width=1024, height=1024)
(here / "icon-1024.png").write_bytes(bytes(png))
print("wrote", here / "icon-1024.png")
```

Run:

```bash
cd apps/desktop && npm install && npm run icon
OUT="$(mktemp -d)" && npx tauri icon src-tauri/icons/icon-1024.png --output "$OUT"
cp "$OUT"/32x32.png "$OUT"/128x128.png "$OUT"/128x128@2x.png "$OUT"/icon.icns "$OUT"/icon.png src-tauri/icons/
```

Expected: the five files in `src-tauri/icons/`. Look at `icon-1024.png` with the Read tool: a dark rounded square with the yellow trace, transparent corners.

- [ ] **Step 5: Build and check nothing else moved**

```bash
cd frontend && npm run build && cd ..
cargo build -p studi0trace-desktop
cargo test --workspace --release
cd backend && .venv/bin/python -m pytest tests/test_vexel_lock_matches_workspace.py -q -o addopts=""
```

Expected: the build succeeds (if it stops at `generate_context!` complaining that `frontendDist` does not exist, the first line was skipped); the workspace tests pass as before (278); the lock test passes. If the lock test fails, run `cd backend && .venv/bin/python -m tools.sync_vexel_lock` and run it again. If any core test fails, a new dependency changed a feature the core relies on: stop and report it.

- [ ] **Step 6: Run the app**

```bash
cd apps/desktop && npx tauri build --debug --bundles app
open -n ../../target/debug/bundle/macos/Studi0Trace.app; sleep 8
pgrep -fl "Studi0Trace.app/Contents/MacOS/studi0trace-desktop"
osascript -e 'quit app "Studi0Trace"'
```

Expected: one `studi0trace-desktop` process while it runs (the window shows the old UI, which cannot reach a server; that is fine here). Then `npm run dev` starts Vite and opens the window (stop it with Ctrl-C).

- [ ] **Step 7: Commit**

```bash
git add Cargo.toml Cargo.lock apps/desktop backend/vexel-rs/Cargo.lock
git commit -m "desktop: the Tauri 2 shell in the workspace, its window, capabilities and icon

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Opening images: errors, the image store and intake

Rust opens a path or bytes, converts what the core cannot read, applies the core's limits, and keeps the bytes under the core's id. The UI gets the image's facts or the refusal, with a code.

**Files:**
- Create: `apps/desktop/src-tauri/src/error.rs`, `src/store.rs`, `src/intake.rs`, `src/commands.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`

**Interfaces:**
- Consumes: `studi0trace_core::api::{Core, ApiError}` (`Core::new()`, `health()`, `engines()`, `presets()`, `upload(&[u8]) -> Result<Value, ApiError>` returning `{image_id, width, height, format}`; `ApiError { status, .. }` and `response_body()`); `studi0trace_core::intake::Limits::default().max_side` (`Some(2048)`).
- Produces:
  - `error::CommandError { status: u16, body: Value }` (serialises as `{"status", "body"}`; `body` is `{"detail": {code, message}}` or the 422's list), `CommandError::new(status, code, message)`, `::cancelled()`, `::crashed(why)`, `::conversion(name, why)`, `::io(path, err)`, `::expired()`, `::bad_request(why)`, `.code()`, and `impl From<ApiError>`.
  - `store::{OpenImage, Opened, Images}`: `OpenImage { id, name, path: Option<PathBuf>, width, height, format, bytes: Arc<Vec<u8>> }`; `Opened` (camelCase JSON `{id, name, path, width, height, format}`); `Images::insert(OpenImage) -> Opened`, `get(&str) -> Option<OpenImage>`, `bytes(&str) -> Option<Arc<Vec<u8>>>`, `remove(&str) -> bool`, `len()`.
  - `intake::{sniff, Kind, open_path, open_bytes, max_side, decode_header_name}`.
  - `commands::Outcome` = `{"ok": Opened}` | `{"failed": {name, path, error: CommandError}}`.
  - Commands: `health`, `engines`, `presets`, `pick_images() -> Vec<Outcome>`, `open_paths(paths: Vec<String>, downscale: bool) -> Vec<Outcome>`, `open_bytes(raw body; header x-name) -> Outcome`, `read_image(id) -> raw bytes`, `close_image(id) -> bool`.
  - `lib.rs`: `pub struct AppState { pub core: Core, pub images: Images }` managed by Tauri.

- [ ] **Step 1: Write the failing tests**

At the foot of `src/error.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_core_refusal_keeps_its_status_and_body() {
        let core = studi0trace_core::api::Core::new();
        let refused = core.upload(b"not an image").unwrap_err();
        let e = CommandError::from(refused.clone());
        assert_eq!((e.status, &e.body), (refused.status, &refused.response_body()));
        assert_eq!(e.code(), Some("unsupported_format"));
    }

    #[test]
    fn the_apps_own_codes() {
        assert_eq!(CommandError::cancelled().code(), Some("cancelled"));
        assert_eq!(CommandError::crashed("signal 9").code(), Some("engine_crashed"));
        assert_eq!(CommandError::expired().code(), Some("image_expired"));
        let e = CommandError::io(std::path::Path::new("/nope/x.png"), &std::io::Error::from(std::io::ErrorKind::NotFound));
        assert_eq!(e.code(), Some("io_error"));
        assert!(e.body["detail"]["message"].as_str().unwrap().starts_with("/nope/x.png: "));
    }
}
```

At the foot of `src/store.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn image(id: &str, path: Option<&str>) -> OpenImage {
        OpenImage { id: id.into(), name: "a.png".into(), path: path.map(PathBuf::from), width: 4, height: 2, format: "PNG".into(), bytes: Arc::new(vec![1, 2, 3]) }
    }

    #[test]
    fn holds_each_file_once_and_lets_it_go() {
        let images = Images::default();
        let opened = images.insert(image("a", None));
        assert_eq!((opened.id.as_str(), opened.width, opened.path.as_deref()), ("a", 4, None));
        // the same file again, now from a path: one entry, which learns the path
        images.insert(image("a", Some("/pics/a.png")));
        assert_eq!(images.len(), 1);
        assert_eq!(images.get("a").unwrap().path.as_deref(), Some(std::path::Path::new("/pics/a.png")));
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[1, 2, 3]);
        assert!(images.remove("a"));
        assert!(!images.remove("a"));
        assert!(images.bytes("a").is_none());
    }

    #[test]
    fn opened_is_camel_case_json() {
        let v = serde_json::to_value(Opened::from(&image("a", Some("/p/a.png")))).unwrap();
        assert_eq!(v, serde_json::json!({"id": "a", "name": "a.png", "path": "/p/a.png", "width": 4, "height": 2, "format": "PNG"}));
    }
}
```

At the foot of `src/intake.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    fn fixture(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../crates/studi0trace-core/tests/fixtures").join(name)
    }

    fn png(w: u32, h: u32) -> Vec<u8> {
        let mut out = std::io::Cursor::new(Vec::new());
        image::RgbaImage::from_pixel(w, h, image::Rgba([200, 30, 30, 255])).write_to(&mut out, image::ImageFormat::Png).unwrap();
        out.into_inner()
    }

    fn sips(src: &Path, format: &str, out: &Path) {
        let ok = std::process::Command::new("/usr/bin/sips").args(["-s", "format", format]).arg(src).arg("--out").arg(out).output().unwrap();
        assert!(ok.status.success(), "{}", String::from_utf8_lossy(&ok.stderr));
    }

    #[test]
    fn sniffs_what_the_core_cannot_read() {
        assert_eq!(sniff(&png(2, 2)), Kind::Native);
        assert_eq!(sniff(b"MM\0*rest"), Kind::Tiff);
        assert_eq!(sniff(b"II*\0rest"), Kind::Tiff);
        assert_eq!(sniff(b"\0\0\0\x24ftypheic\0\0\0\0"), Kind::Heif);
        assert_eq!(sniff(b"\0\0\0\x18ftypmif1"), Kind::Heif);
        assert_eq!(sniff(b"\0\0\0\x18ftypisom"), Kind::Native); // an MP4 is not an image; the core refuses it
    }

    #[test]
    fn opens_a_png_from_a_path() {
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-intake-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("red.png");
        std::fs::write(&path, png(30, 10)).unwrap();
        let img = open_path(&core, &path, false).unwrap();
        assert_eq!((img.name.as_str(), img.width, img.height, img.format.as_str()), ("red.png", 30, 10, "PNG"));
        assert_eq!(img.path.as_deref(), Some(path.as_path()));
        assert_eq!(img.id.len(), 32);
    }

    #[test]
    fn heic_and_tiff_are_converted_and_keep_their_orientation() {
        // intake_exif6.jpg is stored 40 x 20 with EXIF orientation 6: shown 20 x 40
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-heic-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for format in ["heic", "tiff"] {
            let out = dir.join(format!("turned.{format}"));
            sips(&fixture("intake_exif6.jpg"), format, &out);
            let img = open_path(&core, &out, false).unwrap();
            assert_eq!((img.width, img.height, img.format.as_str()), (20, 40, "PNG"), "{format}");
        }
    }

    #[test]
    fn an_image_over_the_cap_is_refused_and_can_be_downscaled() {
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-big-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("wide.png");
        std::fs::write(&path, png(3000, 300)).unwrap();
        let refused = open_path(&core, &path, false).unwrap_err();
        assert_eq!(refused.code(), Some("too_many_pixels"));
        let img = open_path(&core, &path, true).unwrap();
        // sips rounds 204.8 one way or the other
        assert!(img.width == 2048 && (204..=205).contains(&img.height), "{}x{}", img.width, img.height);
    }

    #[test]
    fn bytes_open_like_files_and_a_missing_file_is_an_io_error() {
        let core = Core::new();
        let img = open_bytes(&core, "paste.png", png(8, 8)).unwrap();
        assert_eq!((img.name.as_str(), img.path, img.width), ("paste.png", None, 8));
        let missing = open_path(&core, Path::new("/nonexistent/none.png"), false).unwrap_err();
        assert_eq!(missing.code(), Some("io_error"));
        assert_eq!(open_bytes(&core, "junk.bin", b"junk".to_vec()).unwrap_err().code(), Some("unsupported_format"));
    }

    #[test]
    fn header_names_are_percent_decoded() {
        assert_eq!(decode_header_name("logo%20%C3%A9t%C3%A9.png"), "logo été.png");
        assert_eq!(decode_header_name("plain.png"), "plain.png");
        assert_eq!(decode_header_name("bad%zz.png"), "bad%zz.png");
    }
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p studi0trace-desktop --release --lib`
Expected: compile errors (the modules and items do not exist).

- [ ] **Step 3: Write `error.rs`, `store.rs` and `intake.rs`**

`src/error.rs`:

```rust
//! What a command answers when it fails: the status and the body the Python server would have sent
//! (`{"detail": {"code", "message"}}`, or the 422's list), so the UI reads one shape whoever answered.
use serde::Serialize;
use serde_json::{json, Value};
use std::path::Path;
use studi0trace_core::api::ApiError;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CommandError {
    pub status: u16,
    pub body: Value,
}

impl CommandError {
    pub fn new(status: u16, code: &str, message: impl Into<String>) -> Self {
        CommandError { status, body: json!({ "detail": { "code": code, "message": message.into() } }) }
    }

    pub fn code(&self) -> Option<&str> {
        self.body["detail"]["code"].as_str()
    }

    pub fn cancelled() -> Self {
        Self::new(499, "cancelled", "The trace was cancelled")
    }

    pub fn crashed(why: impl std::fmt::Display) -> Self {
        Self::new(500, "engine_crashed", format!("The trace crashed ({why})"))
    }

    pub fn conversion(name: &str, why: impl std::fmt::Display) -> Self {
        Self::new(400, "conversion_failed", format!("macOS could not convert {name}: {why}"))
    }

    pub fn io(path: &Path, err: &std::io::Error) -> Self {
        Self::new(500, "io_error", format!("{}: {err}", path.display()))
    }

    /// The core's own words for an id it does not hold.
    pub fn expired() -> Self {
        Self::new(404, "image_expired", "Upload expired or unknown; upload it again")
    }

    pub fn bad_request(why: impl Into<String>) -> Self {
        Self::new(400, "bad_request", why)
    }
}

impl From<ApiError> for CommandError {
    fn from(e: ApiError) -> Self {
        CommandError { status: e.status, body: e.response_body() }
    }
}
```

`src/store.rs`:

```rust
//! The images open in the window: the core's id (a hash of the file) to the file's bytes and where it came
//! from. The bytes are the compressed file, a few megabytes at most, so every open image is kept until the UI
//! closes it; a trace hands them to its worker.
use serde::Serialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone)]
pub struct OpenImage {
    pub id: String,
    pub name: String,
    pub path: Option<PathBuf>,
    pub width: u32,
    pub height: u32,
    pub format: String,
    pub bytes: Arc<Vec<u8>>,
}

/// What the UI is told about an image.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Opened {
    pub id: String,
    pub name: String,
    pub path: Option<String>,
    pub width: u32,
    pub height: u32,
    pub format: String,
}

impl From<&OpenImage> for Opened {
    fn from(i: &OpenImage) -> Self {
        Opened {
            id: i.id.clone(),
            name: i.name.clone(),
            path: i.path.as_ref().map(|p| p.display().to_string()),
            width: i.width,
            height: i.height,
            format: i.format.clone(),
        }
    }
}

#[derive(Default)]
pub struct Images {
    inner: Mutex<HashMap<String, OpenImage>>,
}

impl Images {
    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, OpenImage>> {
        self.inner.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Keep `image`; the same file opened again is the same entry, and learns a path it did not have.
    pub fn insert(&self, image: OpenImage) -> Opened {
        let mut map = self.lock();
        let entry = map.entry(image.id.clone()).or_insert_with(|| image.clone());
        if entry.path.is_none() && image.path.is_some() {
            entry.path = image.path;
        }
        Opened::from(&*entry)
    }

    pub fn get(&self, id: &str) -> Option<OpenImage> {
        self.lock().get(id).cloned()
    }

    pub fn bytes(&self, id: &str) -> Option<Arc<Vec<u8>>> {
        self.lock().get(id).map(|i| i.bytes.clone())
    }

    pub fn remove(&self, id: &str) -> bool {
        self.lock().remove(id).is_some()
    }

    pub fn len(&self) -> usize {
        self.lock().len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}
```

`src/intake.rs`:

```rust
//! Opening a file: read it, let macOS convert what the core has no decoder for (HEIC, HEIF, TIFF) or scale
//! down what is over the core's side cap, then hand the bytes to `Core::upload`, which validates them and
//! names them. `sips` writes the EXIF orientation into the PNG it makes, and the core applies it.
use crate::error::CommandError;
use crate::store::OpenImage;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use studi0trace_core::api::Core;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// PNG, JPEG, GIF, WebP, BMP, or anything else: the core decides.
    Native,
    Heif,
    Tiff,
}

const HEIF_BRANDS: &[&[u8; 4]] = &[b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"avif"];

/// What a file is, by its first bytes (the extension is not trusted, as the server never trusted it).
pub fn sniff(bytes: &[u8]) -> Kind {
    if bytes.starts_with(b"II*\0") || bytes.starts_with(b"MM\0*") {
        return Kind::Tiff;
    }
    if bytes.len() >= 12 && &bytes[4..8] == b"ftyp" && HEIF_BRANDS.iter().any(|b| &bytes[8..12] == *b) {
        return Kind::Heif;
    }
    Kind::Native
}

/// The core's cap on a side (2048).
pub fn max_side() -> u32 {
    studi0trace_core::intake::Limits::default().max_side.expect("the core caps a side")
}

static SCRATCH: AtomicU64 = AtomicU64::new(0);

fn scratch(ext: &str) -> PathBuf {
    std::env::temp_dir().join(format!("studi0trace-{}-{}.{ext}", std::process::id(), SCRATCH.fetch_add(1, Ordering::Relaxed)))
}

/// `src` as PNG bytes, made by `sips`, scaled to fit `max_side` if given.
fn convert(name: &str, src: &Path, max_side: Option<u32>) -> Result<Vec<u8>, CommandError> {
    let out = scratch("png");
    let mut cmd = Command::new("/usr/bin/sips");
    cmd.args(["-s", "format", "png"]);
    if let Some(side) = max_side {
        cmd.arg("--resampleHeightWidthMax").arg(side.to_string());
    }
    let done = cmd.arg(src).arg("--out").arg(&out).output().map_err(|e| CommandError::conversion(name, e))?;
    let read = std::fs::read(&out);
    let _ = std::fs::remove_file(&out);
    if !done.status.success() {
        return Err(CommandError::conversion(name, String::from_utf8_lossy(&done.stderr).trim()));
    }
    read.map_err(|e| CommandError::conversion(name, e))
}

fn admit(core: &Core, name: String, path: Option<PathBuf>, bytes: Vec<u8>) -> Result<OpenImage, CommandError> {
    let up: Value = core.upload(&bytes)?;
    Ok(OpenImage {
        id: up["image_id"].as_str().unwrap_or_default().to_string(),
        name,
        path,
        width: up["width"].as_u64().unwrap_or(0) as u32,
        height: up["height"].as_u64().unwrap_or(0) as u32,
        format: up["format"].as_str().unwrap_or_default().to_string(),
        bytes: Arc::new(bytes),
    })
}

fn file_name(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.display().to_string())
}

/// Open the file at `path`; `downscale` scales it to fit the core's side cap first.
pub fn open_path(core: &Core, path: &Path, downscale: bool) -> Result<OpenImage, CommandError> {
    let name = file_name(path);
    let raw = std::fs::read(path).map_err(|e| CommandError::io(path, &e))?;
    let bytes = if downscale {
        convert(&name, path, Some(max_side()))?
    } else if sniff(&raw) != Kind::Native {
        convert(&name, path, None)?
    } else {
        raw
    };
    admit(core, name, Some(path.to_path_buf()), bytes)
}

/// Open bytes that have no file (a sample, a paste).
pub fn open_bytes(core: &Core, name: &str, bytes: Vec<u8>) -> Result<OpenImage, CommandError> {
    if sniff(&bytes) == Kind::Native {
        return admit(core, name.to_string(), None, bytes);
    }
    let tmp = scratch("img");
    std::fs::write(&tmp, &bytes).map_err(|e| CommandError::io(&tmp, &e))?;
    let converted = convert(name, &tmp, None);
    let _ = std::fs::remove_file(&tmp);
    admit(core, name.to_string(), None, converted?)
}

/// A file name the UI sent in a header, where it is percent-encoded (`encodeURIComponent`); left as it is
/// where it does not decode.
pub fn decode_header_name(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).ok().and_then(|h| u8::from_str_radix(h, 16).ok());
            match hex {
                Some(b) => {
                    out.push(b);
                    i += 3;
                    continue;
                }
                None => return s.to_string(),
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8(out).unwrap_or_else(|_| s.to_string())
}
```

- [ ] **Step 4: Write the commands and register them**

`src/commands.rs`:

```rust
//! Every command the UI invokes. Anything that blocks (a panel, a file, a trace) is `async`, which Tauri runs
//! off the main thread; a sync command runs on it.
use crate::error::CommandError;
use crate::intake;
use crate::store::Opened;
use crate::AppState;
use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use tauri::ipc::{InvokeBody, Request, Response};
use tauri::State;
use tauri_plugin_dialog::DialogExt;

/// One file's outcome: `{"ok": {...}}` or `{"failed": {"name", "path", "error"}}`.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Outcome {
    Ok(Opened),
    Failed { name: String, path: Option<String>, error: CommandError },
}

pub const IMAGE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif", "tif", "tiff"];

pub(crate) fn open_one(state: &AppState, path: PathBuf, downscale: bool) -> Outcome {
    match intake::open_path(&state.core, &path, downscale) {
        Ok(image) => Outcome::Ok(state.images.insert(image)),
        Err(error) => Outcome::Failed {
            name: path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
            path: Some(path.display().to_string()),
            error,
        },
    }
}

#[tauri::command]
pub fn health(state: State<'_, AppState>) -> Value {
    state.core.health()
}

#[tauri::command]
pub fn engines(state: State<'_, AppState>) -> Value {
    state.core.engines()
}

#[tauri::command]
pub fn presets(state: State<'_, AppState>) -> Value {
    state.core.presets()
}

#[tauri::command]
pub async fn open_paths(state: State<'_, AppState>, paths: Vec<String>, downscale: bool) -> Result<Vec<Outcome>, CommandError> {
    Ok(paths.into_iter().map(|p| open_one(&state, PathBuf::from(p), downscale)).collect())
}

#[tauri::command]
pub async fn pick_images(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<Vec<Outcome>, CommandError> {
    let picked = app.dialog().file().set_title("Open Images").add_filter("Images", IMAGE_EXTENSIONS).blocking_pick_files().unwrap_or_default();
    Ok(picked.into_iter().filter_map(|f| f.into_path().ok()).map(|p| open_one(&state, p, false)).collect())
}

#[tauri::command]
pub async fn open_bytes(state: State<'_, AppState>, request: Request<'_>) -> Result<Outcome, CommandError> {
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err(CommandError::bad_request("open_bytes takes the file's bytes"));
    };
    let name = request.headers().get("x-name").and_then(|v| v.to_str().ok()).map(intake::decode_header_name).unwrap_or_else(|| "Untitled".into());
    Ok(match intake::open_bytes(&state.core, &name, bytes.clone()) {
        Ok(image) => Outcome::Ok(state.images.insert(image)),
        Err(error) => Outcome::Failed { name, path: None, error },
    })
}

#[tauri::command]
pub async fn read_image(state: State<'_, AppState>, id: String) -> Result<Response, CommandError> {
    let bytes = state.images.bytes(&id).ok_or_else(CommandError::expired)?;
    Ok(Response::new(bytes.as_ref().clone()))
}

#[tauri::command]
pub fn close_image(state: State<'_, AppState>, id: String) -> bool {
    state.images.remove(&id)
}
```

Replace `src/lib.rs` with:

```rust
//! Studi0Trace for Mac: the Tauri shell around `studi0trace_core::api::Core`.
pub mod commands;
pub mod error;
pub mod intake;
pub mod store;
pub mod worker;

use studi0trace_core::api::Core;

/// What every command shares.
pub struct AppState {
    pub core: Core,
    pub images: store::Images,
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        .manage(AppState { core: Core::new(), images: store::Images::default() })
        .invoke_handler(tauri::generate_handler![
            commands::health,
            commands::engines,
            commands::presets,
            commands::open_paths,
            commands::pick_images,
            commands::open_bytes,
            commands::read_image,
            commands::close_image,
        ])
        .run(tauri::generate_context!())
        .expect("Studi0Trace failed to start");
}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cargo test -p studi0trace-desktop --release --lib` then `cargo clippy -p studi0trace-desktop --release --all-targets`
Expected: every test passes (the HEIC and TIFF one shells out to `/usr/bin/sips`); no clippy warnings in the crate.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src-tauri/src
git commit -m "desktop: open images from paths and bytes, HEIC and TIFF through sips, the image store, errors with codes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The trace worker and its queue

Every trace runs in a child process: the app's own binary with `--trace-worker`. One runs at a time; a newer job for the same image replaces or kills the old one; `cancel` kills or dequeues; a worker that dies is `engine_crashed`; quitting the app kills the worker, and a worker whose parent dies exits.

**Files:**
- Modify: `apps/desktop/src-tauri/src/worker.rs` (replace the stub), `src/commands.rs`, `src/lib.rs`
- Create: `apps/desktop/src-tauri/src/queue.rs`, `apps/desktop/src-tauri/tests/worker.rs`

**Interfaces:**
- Consumes: `AppState`, `CommandError` (Task 2); `Core::upload`, `Core::vectorize(id, &Value, auto) -> Result<Value, ApiError>`.
- Produces:
  - Worker protocol: stdin = one JSON line `{"parameters": <vexel parameters>, "auto": bool, "bytes": N, "test": <hook or null>}` then N bytes; stdout = one JSON line `{"ok": <Core::vectorize JSON>}` or `{"err": {"status", "body"}}`. Hooks (honoured only with `STUDI0TRACE_TEST_HOOKS=1`): `{"sleep": {"ms": N}}`, `"die"`, `"garbage"`.
  - `queue::{TraceQueue, JobSpec, Reply, parse}`: `TraceQueue::new(exe)`, `::with_env(exe, Vec<(String, String)>)`, `.submit(JobSpec, on_start) -> mpsc::Receiver<Reply>`, `.cancel(&str) -> bool`, `.shutdown()`. `JobSpec { id, image_id, bytes: Arc<Vec<u8>>, parameters: Value, auto: bool, test: Option<Value> }`.
  - `AppState.queue: TraceQueue`.
  - Commands: `vectorize(image_id, parameters, auto, job) -> Value` (the `Core::vectorize` JSON), `cancel_trace(job) -> bool`. Event `trace-phase` `{job, phase: "tracing"}` when a job's worker starts (a job is "queued" until then).

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src-tauri/tests/worker.rs`:

```rust
//! The worker and the queue on the real binary. The test hooks make a worker sleep, die or babble.
use serde_json::{json, Value};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use studi0trace_desktop::queue::{JobSpec, TraceQueue};

const EXE: &str = env!("CARGO_BIN_EXE_studi0trace-desktop");
const WAIT: Duration = Duration::from_secs(120);

fn fixture(name: &str) -> Vec<u8> {
    std::fs::read(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../crates/studi0trace-core/tests/fixtures").join(name)).unwrap()
}

fn hooked() -> TraceQueue {
    TraceQueue::with_env(PathBuf::from(EXE), vec![("STUDI0TRACE_TEST_HOOKS".into(), "1".into())])
}

fn job(id: &str, image: &str, test: Option<Value>) -> JobSpec {
    JobSpec { id: id.into(), image_id: image.into(), bytes: Arc::new(fixture("intake_png.png")), parameters: json!({}), auto: false, test }
}

fn worker(header: Value, bytes: &[u8]) -> Value {
    let mut child = Command::new(EXE).arg("--trace-worker").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    let mut stdin = child.stdin.take().unwrap();
    writeln!(stdin, "{header}").unwrap();
    stdin.write_all(bytes).unwrap();
    drop(stdin);
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    serde_json::from_slice(&out.stdout).unwrap()
}

#[test]
fn the_worker_answers_as_the_core_does() {
    let png = fixture("intake_png.png");
    let answer = worker(json!({"parameters": {}, "auto": false, "bytes": png.len()}), &png);
    let core = studi0trace_core::api::Core::new();
    let id = core.upload(&png).unwrap()["image_id"].as_str().unwrap().to_string();
    let want = core.vectorize(&id, &json!({}), false).unwrap();
    assert_eq!(answer["ok"]["results"]["vexel"]["svg"], want["results"]["vexel"]["svg"]);
    assert_eq!(answer["ok"]["width"], want["width"]);
}

#[test]
fn a_refusal_comes_back_with_its_status_and_body() {
    let png = fixture("intake_png.png");
    let answer = worker(json!({"parameters": {"detail": "lots"}, "auto": false, "bytes": png.len()}), &png);
    assert_eq!(answer["err"]["status"], 422);
    assert!(answer["err"]["body"]["detail"].is_array(), "{answer}");
}

#[test]
fn one_worker_at_a_time_in_order() {
    let q = hooked();
    let started: Arc<Mutex<Vec<(&str, Instant)>>> = Arc::default();
    let (s1, s2) = (started.clone(), started.clone());
    let a = q.submit(job("a", "img-a", Some(json!({"sleep": {"ms": 600}}))), move || s1.lock().unwrap().push(("a", Instant::now())));
    let b = q.submit(job("b", "img-b", None), move || s2.lock().unwrap().push(("b", Instant::now())));
    assert!(a.recv_timeout(WAIT).unwrap().is_ok());
    assert!(b.recv_timeout(WAIT).unwrap().is_ok());
    let s = started.lock().unwrap();
    assert_eq!(s.iter().map(|x| x.0).collect::<Vec<_>>(), ["a", "b"]);
    assert!(s[1].1.duration_since(s[0].1) >= Duration::from_millis(600), "b started while a ran");
}

#[test]
fn a_newer_job_for_an_image_kills_the_running_one() {
    let q = hooked();
    let old = q.submit(job("old", "img", Some(json!({"sleep": {"ms": 30000}}))), || {});
    std::thread::sleep(Duration::from_millis(300));
    let t = Instant::now();
    let new = q.submit(job("new", "img", None), || {});
    assert_eq!(old.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert!(t.elapsed() < Duration::from_secs(3), "{:?}", t.elapsed());
    assert!(new.recv_timeout(WAIT).unwrap().is_ok());
}

#[test]
fn a_queued_job_for_the_same_image_is_replaced() {
    let q = hooked();
    let busy = q.submit(job("busy", "other", Some(json!({"sleep": {"ms": 1500}}))), || {});
    std::thread::sleep(Duration::from_millis(200));
    let first = q.submit(job("first", "img", None), || {});
    let second = q.submit(job("second", "img", None), || {});
    assert_eq!(first.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert!(busy.recv_timeout(WAIT).unwrap().is_ok());
    assert!(second.recv_timeout(WAIT).unwrap().is_ok());
}

#[test]
fn cancel_kills_a_running_job_and_drops_a_queued_one() {
    let q = hooked();
    let running = q.submit(job("r", "a", Some(json!({"sleep": {"ms": 30000}}))), || {});
    let queued = q.submit(job("q", "b", None), || {});
    std::thread::sleep(Duration::from_millis(300));
    assert!(q.cancel("q"));
    assert_eq!(queued.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    let t = Instant::now();
    assert!(q.cancel("r"));
    assert_eq!(running.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert!(t.elapsed() < Duration::from_secs(3), "{:?}", t.elapsed());
    assert!(!q.cancel("nope"));
}

#[test]
fn a_worker_that_dies_or_babbles_is_a_crashed_trace() {
    let q = hooked();
    for hook in [json!("die"), json!("garbage")] {
        let r = q.submit(job("x", "img", Some(hook.clone())), || {}).recv_timeout(WAIT).unwrap();
        assert_eq!(r.unwrap_err().code(), Some("engine_crashed"), "{hook}");
    }
}

#[test]
fn hooks_are_ignored_without_the_variable() {
    let q = TraceQueue::new(PathBuf::from(EXE));
    let t = Instant::now();
    let r = q.submit(job("x", "img", Some(json!({"sleep": {"ms": 30000}}))), || {}).recv_timeout(WAIT).unwrap();
    assert!(r.is_ok() && t.elapsed() < Duration::from_secs(20));
}

#[test]
fn shutdown_kills_the_running_worker_and_the_queue() {
    let q = hooked();
    let running = q.submit(job("r", "a", Some(json!({"sleep": {"ms": 30000}}))), || {});
    let queued = q.submit(job("q", "b", None), || {});
    std::thread::sleep(Duration::from_millis(300));
    q.shutdown();
    assert_eq!(running.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
    assert_eq!(queued.recv_timeout(WAIT).unwrap().unwrap_err().code(), Some("cancelled"));
}
```

At the foot of `src/queue.rs` (unit tests of the answer parser):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_workers_line() {
        assert_eq!(parse("{\"ok\": {\"a\": 1}}\n").unwrap().unwrap(), json!({"a": 1}));
        let err = parse("{\"err\": {\"status\": 422, \"body\": {\"detail\": []}}}").unwrap().unwrap_err();
        assert_eq!((err.status, err.body), (422, json!({"detail": []})));
        assert!(parse("").is_none());
        assert!(parse("not json").is_none());
        assert!(parse("{\"neither\": 1}").is_none());
    }
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p studi0trace-desktop --release`
Expected: compile errors (`queue` does not exist).

- [ ] **Step 3: Write the worker**

`src/worker.rs`:

```rust
//! The trace worker: this binary started with `--trace-worker`, one per trace. It reads one job from stdin
//! (a JSON line, then the file's bytes), traces it with a `Core` of its own, writes one JSON line to stdout and
//! exits. Killing it cancels the trace, its exit gives back the memory, and its crash is a failed trace, not a
//! closed window. It never touches AppKit, so it never shows in the Dock.
use crate::error::CommandError;
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{BufRead, Read, Write};
use std::time::Duration;
use studi0trace_core::api::Core;

pub const FLAG: &str = "--trace-worker";
/// With this set to "1", a job's `test` hook is obeyed; otherwise it is ignored.
pub const TEST_HOOKS: &str = "STUDI0TRACE_TEST_HOOKS";

#[derive(Debug, Deserialize)]
struct Header {
    parameters: Value,
    auto: bool,
    bytes: usize,
    #[serde(default)]
    test: Option<TestHook>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
enum TestHook {
    Sleep { ms: u64 },
    Die,
    Garbage,
}

/// The answer to one job, as the worker writes it.
pub fn answer(core: &Core, bytes: &[u8], parameters: &Value, auto: bool) -> Value {
    let traced = core.upload(bytes).and_then(|up| {
        let id = up["image_id"].as_str().unwrap_or_default().to_string();
        core.vectorize(&id, parameters, auto)
    });
    match traced {
        Ok(v) => json!({ "ok": v }),
        Err(e) => json!({ "err": CommandError::from(e) }),
    }
}

/// Exit when the app that started this worker is gone (it is then the child of launchd).
fn exit_with_parent() {
    let parent = std::os::unix::process::parent_id();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(500));
        if std::os::unix::process::parent_id() != parent {
            std::process::exit(4);
        }
    });
}

pub fn main() -> i32 {
    exit_with_parent();
    let mut input = std::io::stdin().lock();
    let mut line = String::new();
    if input.read_line(&mut line).is_err() {
        eprintln!("trace worker: no job");
        return 2;
    }
    let header: Header = match serde_json::from_str(&line) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("trace worker: a bad job header: {e}");
            return 2;
        }
    };
    let mut bytes = vec![0; header.bytes];
    if let Err(e) = input.read_exact(&mut bytes) {
        eprintln!("trace worker: a short job: {e}");
        return 2;
    }
    if std::env::var(TEST_HOOKS).as_deref() == Ok("1") {
        match header.test {
            Some(TestHook::Sleep { ms }) => std::thread::sleep(Duration::from_millis(ms)),
            Some(TestHook::Die) => std::process::exit(70),
            Some(TestHook::Garbage) => {
                println!("this is not an answer");
                return 0;
            }
            None => {}
        }
    }
    let out = answer(&Core::new(), &bytes, &header.parameters, header.auto);
    let mut stdout = std::io::stdout().lock();
    if writeln!(stdout, "{out}").and_then(|_| stdout.flush()).is_err() {
        return 3;
    }
    0
}
```

- [ ] **Step 4: Write the queue**

`src/queue.rs`:

```rust
//! The app's side of the trace worker. One worker runs at a time (Auto at 2048 px peaks near 11 GB; two at
//! once is a Mac swapping). A newer job for an image replaces its queued job, or kills its running one; `cancel`
//! kills or dequeues. A worker that exits without an answer is `engine_crashed`, or `cancelled` where this side
//! killed it. The running job is registered before its worker is spawned, so a cancel never misses it.
use crate::error::CommandError;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

pub type Reply = Result<Value, CommandError>;

pub struct JobSpec {
    pub id: String,
    pub image_id: String,
    pub bytes: Arc<Vec<u8>>,
    pub parameters: Value,
    pub auto: bool,
    /// A worker test hook as JSON; obeyed only where the worker's environment allows it.
    pub test: Option<Value>,
}

struct Job {
    spec: JobSpec,
    reply: mpsc::Sender<Reply>,
    on_start: Box<dyn FnOnce() + Send>,
}

struct Running {
    id: String,
    image_id: String,
    child: Arc<Mutex<Option<Child>>>,
    cancelled: Arc<AtomicBool>,
}

#[derive(Default)]
struct State {
    waiting: VecDeque<Job>,
    running: Option<Running>,
}

struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    exe: PathBuf,
    env: Vec<(String, String)>,
}

#[derive(Clone)]
pub struct TraceQueue {
    shared: Arc<Shared>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn kill(r: &Running) {
    r.cancelled.store(true, Ordering::SeqCst);
    if let Some(child) = lock(&r.child).as_mut() {
        let _ = child.kill();
    }
}

fn refuse(job: &Job) {
    let _ = job.reply.send(Err(CommandError::cancelled()));
}

impl TraceQueue {
    /// A queue whose workers are `exe --trace-worker`.
    pub fn new(exe: PathBuf) -> TraceQueue {
        TraceQueue::with_env(exe, Vec::new())
    }

    /// The same, with variables added to each worker's environment (the tests' hooks).
    pub fn with_env(exe: PathBuf, env: Vec<(String, String)>) -> TraceQueue {
        let shared = Arc::new(Shared { state: Mutex::default(), wake: Condvar::new(), exe, env });
        let runner = shared.clone();
        std::thread::Builder::new().name("trace-queue".into()).spawn(move || run(&runner)).expect("the trace queue's thread starts");
        TraceQueue { shared }
    }

    /// Queue `spec`; `on_start` is called when its worker starts.
    pub fn submit(&self, spec: JobSpec, on_start: impl FnOnce() + Send + 'static) -> mpsc::Receiver<Reply> {
        let (tx, rx) = mpsc::channel();
        let mut st = lock(&self.shared.state);
        st.waiting.retain(|j| {
            let same = j.spec.image_id == spec.image_id;
            if same {
                refuse(j);
            }
            !same
        });
        if let Some(r) = st.running.as_ref().filter(|r| r.image_id == spec.image_id) {
            kill(r);
        }
        st.waiting.push_back(Job { spec, reply: tx, on_start: Box::new(on_start) });
        self.shared.wake.notify_one();
        rx
    }

    /// Cancel the job `id`, running or queued; false when there is no such job.
    pub fn cancel(&self, id: &str) -> bool {
        let mut st = lock(&self.shared.state);
        if let Some(r) = st.running.as_ref().filter(|r| r.id == id) {
            kill(r);
            return true;
        }
        let before = st.waiting.len();
        st.waiting.retain(|j| {
            let hit = j.spec.id == id;
            if hit {
                refuse(j);
            }
            !hit
        });
        st.waiting.len() != before
    }

    /// Kill the running worker and drop every queued job (the app is quitting).
    pub fn shutdown(&self) {
        let mut st = lock(&self.shared.state);
        st.waiting.drain(..).for_each(|j| refuse(&j));
        if let Some(r) = st.running.as_ref() {
            kill(r);
        }
    }
}

fn run(shared: &Shared) {
    loop {
        let (job, child, cancelled) = {
            let mut st = lock(&shared.state);
            let job = loop {
                if let Some(j) = st.waiting.pop_front() {
                    break j;
                }
                st = shared.wake.wait(st).unwrap_or_else(PoisonError::into_inner);
            };
            let child = Arc::new(Mutex::new(None));
            let cancelled = Arc::new(AtomicBool::new(false));
            st.running = Some(Running { id: job.spec.id.clone(), image_id: job.spec.image_id.clone(), child: child.clone(), cancelled: cancelled.clone() });
            (job, child, cancelled)
        };
        let Job { spec, reply, on_start } = job;
        let answer = trace(shared, spec, on_start, &child, &cancelled);
        lock(&shared.state).running = None;
        let _ = reply.send(answer);
    }
}

fn trace(shared: &Shared, spec: JobSpec, on_start: Box<dyn FnOnce() + Send>, slot: &Mutex<Option<Child>>, cancelled: &AtomicBool) -> Reply {
    let mut cmd = Command::new(&shared.exe);
    cmd.arg(crate::worker::FLAG).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    for (k, v) in &shared.env {
        cmd.env(k, v);
    }
    let mut child = cmd.spawn().map_err(|e| CommandError::crashed(format!("the worker did not start: {e}")))?;
    let (mut stdin, mut stdout, mut stderr) = (child.stdin.take().unwrap(), child.stdout.take().unwrap(), child.stderr.take().unwrap());
    *lock(slot) = Some(child);
    if cancelled.load(Ordering::SeqCst) {
        if let Some(c) = lock(slot).as_mut() {
            let _ = c.kill();
        }
    }
    on_start();

    let header = json!({ "parameters": spec.parameters, "auto": spec.auto, "bytes": spec.bytes.len(), "test": spec.test });
    let bytes = spec.bytes.clone();
    // written on its own thread: a worker that dies early must not leave this one blocked on a full pipe
    let writer = std::thread::spawn(move || -> std::io::Result<()> {
        writeln!(stdin, "{header}")?;
        stdin.write_all(&bytes)
    });
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stdout.read_to_string(&mut s);
        s
    });
    let errors = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stderr.read_to_string(&mut s);
        s
    });
    let status = loop {
        let polled = lock(slot).as_mut().map(|c| c.try_wait());
        match polled {
            Some(Ok(Some(status))) => break Ok(status),
            Some(Err(e)) => break Err(e),
            _ => std::thread::sleep(Duration::from_millis(10)),
        }
    };
    let _ = writer.join();
    let out = reader.join().unwrap_or_default();
    let err_text = errors.join().unwrap_or_default();
    if cancelled.load(Ordering::SeqCst) {
        return Err(CommandError::cancelled());
    }
    parse(&out).unwrap_or_else(|| {
        let why = match status {
            Ok(s) => s.to_string(),
            Err(e) => e.to_string(),
        };
        if !err_text.trim().is_empty() {
            eprintln!("trace worker ({why}): {}", err_text.trim());
        }
        Err(CommandError::crashed(why))
    })
}

/// The worker's answer line; None when it wrote none that reads.
pub fn parse(out: &str) -> Option<Reply> {
    let v: Value = serde_json::from_str(out.lines().next()?).ok()?;
    if let Some(ok) = v.get("ok") {
        return Some(Ok(ok.clone()));
    }
    let err = v.get("err")?;
    Some(Err(CommandError { status: err["status"].as_u64()? as u16, body: err["body"].clone() }))
}
```

- [ ] **Step 5: Add the commands and the state**

Append to `src/commands.rs`:

```rust
use crate::queue::JobSpec;
use tauri::Emitter;

#[tauri::command]
pub async fn vectorize(app: tauri::AppHandle, state: State<'_, AppState>, image_id: String, parameters: Value, auto: bool, job: String) -> Result<Value, CommandError> {
    let bytes = state.images.bytes(&image_id).ok_or_else(CommandError::expired)?;
    let (emitter, job_id) = (app.clone(), job.clone());
    let rx = state.queue.submit(JobSpec { id: job, image_id, bytes, parameters, auto, test: None }, move || {
        let _ = emitter.emit("trace-phase", serde_json::json!({ "job": job_id, "phase": "tracing" }));
    });
    tauri::async_runtime::spawn_blocking(move || rx.recv().unwrap_or_else(|_| Err(CommandError::cancelled())))
        .await
        .unwrap_or_else(|e| Err(CommandError::crashed(e)))
}

#[tauri::command]
pub fn cancel_trace(state: State<'_, AppState>, job: String) -> bool {
    state.queue.cancel(&job)
}
```

In `src/lib.rs`: add `pub mod queue;`; add `pub queue: queue::TraceQueue` to `AppState`; build it with `queue::TraceQueue::new(std::env::current_exe().expect("the app knows where it is"))`; add `commands::vectorize, commands::cancel_trace` to the handler; and replace `.run(tauri::generate_context!()).expect(…)` with:

```rust
        .build(tauri::generate_context!())
        .expect("Studi0Trace failed to start")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                use tauri::Manager;
                app.state::<AppState>().queue.shutdown();
            }
        });
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `cargo test -p studi0trace-desktop --release` then `cargo clippy -p studi0trace-desktop --release --all-targets`
Expected: every test passes (`tests/worker.rs` builds and spawns the real binary); no clippy warnings.

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src-tauri
git commit -m "desktop: every trace in a worker process, one at a time, superseded, cancelled by a kill

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Export, Show in Finder and Copy

The UI hands over bytes and a suggested name; Rust picks the place and writes.

**Files:**
- Create: `apps/desktop/src-tauri/src/export.rs`
- Modify: `apps/desktop/src-tauri/src/commands.rs`, `src/lib.rs`

**Interfaces:**
- Consumes: `AppState.images` (an image's `path`), `CommandError`, `intake::decode_header_name`.
- Produces:
  - `export::{safe_name(name, fallback_ext) -> String, unique_path(dir, name) -> PathBuf, write_file(path, bytes) -> Result<(), CommandError>, Destination::{Ask, Beside}, Destination::parse(&str), beside(original, destination, name) -> Option<PathBuf>}`
  - Commands: `export_file` (raw body = the file's bytes; headers `x-kind` `svg|png`, `x-name` (percent-encoded), `x-image` (the image's id), `x-destination` `ask|beside`, `x-reveal` `1|0`) → `string | null` (the written path, or null when the save panel was cancelled); `export_all({items: [{name, svg}], reveal}) -> string[] | null`; `reveal({path})`; `copy_text({text})`.

- [ ] **Step 1: Write the failing tests**

At the foot of `src/export.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn temp(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("s0t-export-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn names_are_made_safe() {
        assert_eq!(safe_name("logo.svg", "svg"), "logo.svg");
        assert_eq!(safe_name("a/b:c.svg", "svg"), "a-b-c.svg");
        assert_eq!(safe_name("..hidden.svg", "svg"), "hidden.svg");
        assert_eq!(safe_name("   ", "png"), "Untitled.png");
    }

    #[test]
    fn copies_are_named_as_finder_names_them() {
        let dir = temp("unique");
        assert_eq!(unique_path(&dir, "logo.svg"), dir.join("logo.svg"));
        std::fs::write(dir.join("logo.svg"), "1").unwrap();
        assert_eq!(unique_path(&dir, "logo.svg"), dir.join("logo 2.svg"));
        std::fs::write(dir.join("logo 2.svg"), "2").unwrap();
        assert_eq!(unique_path(&dir, "logo.svg"), dir.join("logo 3.svg"));
        std::fs::write(dir.join("README"), "x").unwrap();
        assert_eq!(unique_path(&dir, "README"), dir.join("README 2"));
    }

    #[test]
    fn writes_whole_files_and_leaves_nothing_behind() {
        let dir = temp("write");
        let path = dir.join("out.svg");
        write_file(&path, b"<svg/>").unwrap();
        write_file(&path, b"<svg>2</svg>").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"<svg>2</svg>");
        let names: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(names, vec![std::ffi::OsString::from("out.svg")]);
        assert_eq!(write_file(Path::new("/nonexistent-dir/x.svg"), b"x").unwrap_err().code(), Some("io_error"));
    }

    #[test]
    fn beside_the_original_only_when_asked_and_possible() {
        let dir = temp("beside");
        let original = dir.join("logo.png");
        assert_eq!(beside(Some(&original), Destination::Beside, "logo.svg"), Some(dir.join("logo.svg")));
        assert_eq!(beside(Some(&original), Destination::Ask, "logo.svg"), None);
        assert_eq!(beside(None, Destination::Beside, "logo.svg"), None);
        assert_eq!(Destination::parse("beside"), Destination::Beside);
        assert_eq!(Destination::parse("anything"), Destination::Ask);
    }
}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p studi0trace-desktop --release --lib export`
Expected: compile errors.

- [ ] **Step 3: Write `export.rs`**

```rust
//! Writing exports. The UI hands over the bytes and a suggested name; this side picks the place (a save panel,
//! or beside the original, named as Finder names copies) and writes, so the webview never names a path to write.
use crate::error::CommandError;
use std::path::{Path, PathBuf};

/// A suggested file name made safe to write: no folders, not hidden, not empty.
pub fn safe_name(name: &str, fallback_ext: &str) -> String {
    let cleaned: String = name.chars().map(|c| if matches!(c, '/' | ':' | '\0') { '-' } else { c }).collect();
    let trimmed = cleaned.trim().trim_start_matches('.');
    if trimmed.is_empty() {
        format!("Untitled.{fallback_ext}")
    } else {
        trimmed.to_string()
    }
}

/// `dir/name`, or the first of `dir/stem 2.ext`, `dir/stem 3.ext`, … that is free.
pub fn unique_path(dir: &Path, name: &str) -> PathBuf {
    let first = dir.join(name);
    if !first.exists() {
        return first;
    }
    let p = Path::new(name);
    let stem = p.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| name.to_string());
    let ext = p.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    (2..).map(|n| dir.join(format!("{stem} {n}{ext}"))).find(|c| !c.exists()).expect("a free name")
}

/// Write through a temporary file in the same folder, so nothing ever sees half a file.
pub fn write_file(path: &Path, bytes: &[u8]) -> Result<(), CommandError> {
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let tmp = path.with_file_name(format!(".{name}.studi0trace-tmp"));
    std::fs::write(&tmp, bytes).and_then(|_| std::fs::rename(&tmp, path)).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        CommandError::io(path, &e)
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Destination {
    Ask,
    Beside,
}

impl Destination {
    pub fn parse(s: &str) -> Destination {
        if s == "beside" {
            Destination::Beside
        } else {
            Destination::Ask
        }
    }
}

/// Where an export goes without a panel: beside the original, when there is one and the setting says so.
pub fn beside(original: Option<&Path>, destination: Destination, name: &str) -> Option<PathBuf> {
    match (destination, original.and_then(Path::parent)) {
        (Destination::Beside, Some(dir)) => Some(unique_path(dir, name)),
        _ => None,
    }
}
```

- [ ] **Step 4: Add the commands**

Append to `src/commands.rs`:

```rust
use crate::export;
use serde::Deserialize;
use std::path::Path;
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_opener::OpenerExt;

fn header(request: &Request<'_>, key: &str) -> Option<String> {
    request.headers().get(key).and_then(|v| v.to_str().ok()).map(intake::decode_header_name)
}

fn panel_path(fp: tauri_plugin_dialog::FilePath) -> Result<PathBuf, CommandError> {
    fp.into_path().map_err(|e| CommandError::bad_request(format!("not a file path: {e}")))
}

#[tauri::command]
pub async fn export_file(app: tauri::AppHandle, state: State<'_, AppState>, request: Request<'_>) -> Result<Option<String>, CommandError> {
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err(CommandError::bad_request("export_file takes the file's bytes"));
    };
    let ext = if header(&request, "x-kind").as_deref() == Some("png") { "png" } else { "svg" };
    let name = export::safe_name(&header(&request, "x-name").unwrap_or_default(), ext);
    let original = header(&request, "x-image").and_then(|id| state.images.get(&id)).and_then(|i| i.path);
    let destination = export::Destination::parse(&header(&request, "x-destination").unwrap_or_default());
    let path = match export::beside(original.as_deref(), destination, &name) {
        Some(p) => p,
        None => {
            let mut panel = app.dialog().file().set_title(if ext == "png" { "Export PNG" } else { "Export SVG" }).set_file_name(&name).add_filter(if ext == "png" { "PNG image" } else { "SVG image" }, &[ext]);
            if let Some(dir) = original.as_deref().and_then(Path::parent) {
                panel = panel.set_directory(dir);
            }
            match panel.blocking_save_file() {
                Some(fp) => panel_path(fp)?,
                None => return Ok(None),
            }
        }
    };
    export::write_file(&path, bytes)?;
    if header(&request, "x-reveal").as_deref() == Some("1") {
        let _ = app.opener().reveal_item_in_dir(&path);
    }
    Ok(Some(path.display().to_string()))
}

#[derive(Debug, Deserialize)]
pub struct ExportItem {
    pub name: String,
    pub svg: String,
}

#[tauri::command]
pub async fn export_all(app: tauri::AppHandle, items: Vec<ExportItem>, reveal: bool) -> Result<Option<Vec<String>>, CommandError> {
    let Some(dir) = app.dialog().file().set_title("Export All").blocking_pick_folder() else {
        return Ok(None);
    };
    let dir = panel_path(dir)?;
    let mut written = Vec::new();
    for item in items {
        let path = export::unique_path(&dir, &export::safe_name(&item.name, "svg"));
        export::write_file(&path, item.svg.as_bytes())?;
        written.push(path.display().to_string());
    }
    if reveal {
        if let Some(first) = written.first() {
            let _ = app.opener().reveal_item_in_dir(first);
        }
    }
    Ok(Some(written))
}

#[tauri::command]
pub async fn reveal(app: tauri::AppHandle, path: String) -> Result<(), CommandError> {
    let p = PathBuf::from(&path);
    if !p.exists() {
        return Err(CommandError::io(&p, &std::io::Error::from(std::io::ErrorKind::NotFound)));
    }
    app.opener().reveal_item_in_dir(&p).map_err(|e| CommandError::new(500, "io_error", e.to_string()))
}

#[tauri::command]
pub fn copy_text(app: tauri::AppHandle, text: String) -> Result<(), CommandError> {
    app.clipboard().write_text(text).map_err(|e| CommandError::new(500, "io_error", e.to_string()))
}
```

In `src/lib.rs` add `pub mod export;` and the four commands to the handler.

- [ ] **Step 5: Run the tests to see them pass**

Run: `cargo test -p studi0trace-desktop --release` then `cargo clippy -p studi0trace-desktop --release --all-targets`
Expected: PASS, no warnings.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src-tauri/src
git commit -m "desktop: export through a save panel or beside the original, export all, Show in Finder, copy

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The menu bar, settings, recent files and opening from Finder

**Files:**
- Create: `apps/desktop/src-tauri/src/menu.rs`, `src/settings.rs`, `src/opens.rs`
- Modify: `apps/desktop/src-tauri/src/commands.rs`, `src/lib.rs`, `apps/desktop/src-tauri/Cargo.toml` (dev-dependency `muda = "0.20"`)

**Interfaces:**
- Consumes: `commands::open_one`, `AppState`, `CommandError`.
- Produces:
  - `settings::Settings` (camelCase JSON `{appearance: "system"|"light"|"dark", exportTo: "ask"|"beside", revealAfterExport, traceOnOpen, liveUpdate, recent: string[]}`, defaults `system, ask, false, false, true, []`), `Settings::with_recent(path)`, `Settings::merged(from_ui)`, `settings::{load, save, note_recent, clear_recent}`. Event `settings-changed` (payload: `Settings`) to every window on each save.
  - `menu::{MENU, MenuState, plan, build, set_recent, apply_state, on_menu}`; the menu ids below. Event `menu` `{id}` to the main window for every id the UI handles.
  - `opens::Opens` and `opens::deliver(app, paths)`. Event `open-paths` (payload: `string[]`) for paths from Finder (Open With, the Dock), File ▸ Open…, Open Recent and drops on the window; event `drag-state` (payload: `bool`) while files are dragged over the window.
  - Commands: `load_settings() -> Settings`, `save_settings({settings}) -> Settings`, `set_menu_state({state})`, `take_pending_opens() -> string[]`, `open_settings_window()`.
  - `MenuState` JSON (camelCase): `{hasItems, hasImage, hasVector, anyVector, hasPath, tracing, mode: "split"|"side"|"overlay"|"vector", sidebar, inspector}`.

Menu ids the UI receives in `menu` events: `export-svg`, `export-png-1`, `export-png-2`, `export-png-4`, `export-all`, `reveal`, `copy-svg`, `zoom-in`, `zoom-out`, `zoom-actual`, `zoom-fit`, `mode-split`, `mode-side`, `mode-overlay`, `mode-vector`, `toggle-sidebar`, `toggle-inspector`, `generate`, `cancel`, `remove`, `clear`. Rust handles `open`, `recent-0` … `recent-9`, `clear-recent`, `settings`, `help` itself.

- [ ] **Step 1: Write the failing tests**

At the foot of `src/settings.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_and_camel_case() {
        let v = serde_json::to_value(Settings::default()).unwrap();
        assert_eq!(v, serde_json::json!({"appearance": "system", "exportTo": "ask", "revealAfterExport": false, "traceOnOpen": false, "liveUpdate": true, "recent": []}));
        // a file from an older version, missing keys, still reads
        let old: Settings = serde_json::from_value(serde_json::json!({"appearance": "dark"})).unwrap();
        assert_eq!((old.appearance.as_str(), old.live_update), ("dark", true));
    }

    #[test]
    fn recent_files_come_first_once_and_ten_at_most() {
        let mut s = Settings::default();
        for i in 0..12 {
            s = s.with_recent(&format!("/p/{i}.png"));
        }
        assert_eq!(s.recent.len(), MAX_RECENT);
        assert_eq!(s.recent[0], "/p/11.png");
        s = s.with_recent("/p/5.png");
        assert_eq!(s.recent[0], "/p/5.png");
        assert_eq!(s.recent.iter().filter(|p| *p == "/p/5.png").count(), 1);
    }

    #[test]
    fn the_ui_cannot_rewrite_the_recent_list() {
        let stored = Settings::default().with_recent("/p/a.png");
        let from_ui = Settings { appearance: "light".into(), recent: vec!["/etc/passwd".into()], ..Settings::default() };
        let merged = stored.merged(from_ui);
        assert_eq!((merged.appearance.as_str(), merged.recent), ("light", vec!["/p/a.png".to_string()]));
    }
}
```

At the foot of `src/menu.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    #[test]
    fn every_id_is_unique_and_every_accelerator_parses() {
        let mut seen = std::collections::HashSet::new();
        for e in MENU {
            assert!(seen.insert(e.id), "{} twice", e.id);
            if let Some(a) = e.accel {
                assert!(muda::accelerator::Accelerator::from_str(a).is_ok(), "{}: {a}", e.id);
            }
        }
    }

    #[test]
    fn the_state_decides_what_is_enabled_and_checked() {
        let none = plan(&MenuState::default());
        let get = |p: &[(&'static str, bool, Option<bool>)], id: &str| *p.iter().find(|x| x.0 == id).unwrap();
        assert_eq!(get(&none, "export-svg"), ("export-svg", false, None));
        assert_eq!(get(&none, "generate"), ("generate", false, None));
        let s = MenuState { has_items: true, has_image: true, has_vector: true, any_vector: true, has_path: true, tracing: false, mode: "side".into(), sidebar: true, inspector: false };
        let p = plan(&s);
        assert_eq!(get(&p, "export-svg").1, true);
        assert_eq!(get(&p, "reveal").1, true);
        assert_eq!(get(&p, "generate").1, true);
        assert_eq!(get(&p, "cancel").1, false);
        assert_eq!(get(&p, "mode-side"), ("mode-side", true, Some(true)));
        assert_eq!(get(&p, "mode-split").2, Some(false));
        assert_eq!(get(&p, "toggle-sidebar").2, Some(true));
        assert_eq!(get(&p, "toggle-inspector").2, Some(false));
        let tracing = plan(&MenuState { tracing: true, ..s });
        assert_eq!((get(&tracing, "generate").1, get(&tracing, "cancel").1), (false, true));
    }
}
```

At the foot of `src/opens.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_wait_until_the_ui_asks_for_them() {
        let mut o = Opens::default();
        assert_eq!(o.offer(vec!["/a.png".into()]), None);
        assert_eq!(o.offer(vec!["/b.png".into()]), None);
        assert_eq!(o.take(), vec!["/a.png".to_string(), "/b.png".to_string()]);
        assert_eq!(o.offer(vec!["/c.png".into()]), Some(vec!["/c.png".to_string()]));
        assert!(o.take().is_empty());
    }
}
```

Add to `[dev-dependencies]` in `apps/desktop/src-tauri/Cargo.toml`: `muda = "0.20"` (the menu crate under tauri 2.12; only its accelerator parser is used, in this test).

- [ ] **Step 2: Run them to see them fail**

Run: `cargo test -p studi0trace-desktop --release --lib`
Expected: compile errors.

- [ ] **Step 3: Write `settings.rs` and `opens.rs`**

`src/settings.rs`:

```rust
//! The app's settings, in `settings.json` (tauri-plugin-store, in the app's data folder). Every save is sent to
//! every window as `settings-changed`. The recent-files list is kept here too, but only opening files and Clear
//! Menu change it: the UI cannot hand back a list of its own.
use crate::error::CommandError;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Runtime};
use tauri_plugin_store::StoreExt;

pub const MAX_RECENT: usize = 10;
const FILE: &str = "settings.json";
const KEY: &str = "settings";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct Settings {
    pub appearance: String,
    pub export_to: String,
    pub reveal_after_export: bool,
    pub trace_on_open: bool,
    pub live_update: bool,
    pub recent: Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings { appearance: "system".into(), export_to: "ask".into(), reveal_after_export: false, trace_on_open: false, live_update: true, recent: Vec::new() }
    }
}

impl Settings {
    pub fn with_recent(mut self, path: &str) -> Settings {
        self.recent.retain(|p| p != path);
        self.recent.insert(0, path.to_string());
        self.recent.truncate(MAX_RECENT);
        self
    }

    /// The UI's settings, with the recent list kept as it is here.
    pub fn merged(self, from_ui: Settings) -> Settings {
        Settings { recent: self.recent, ..from_ui }
    }
}

pub fn load<R: Runtime>(app: &AppHandle<R>) -> Settings {
    app.store(FILE).ok().and_then(|s| s.get(KEY)).and_then(|v| serde_json::from_value(v).ok()).unwrap_or_default()
}

pub fn save<R: Runtime>(app: &AppHandle<R>, settings: &Settings) -> Result<(), CommandError> {
    let store = app.store(FILE).map_err(|e| CommandError::new(500, "io_error", e.to_string()))?;
    store.set(KEY, serde_json::to_value(settings).expect("settings are JSON"));
    store.save().map_err(|e| CommandError::new(500, "io_error", e.to_string()))?;
    let _ = app.emit("settings-changed", settings);
    crate::menu::set_recent(app, &settings.recent);
    Ok(())
}

pub fn note_recent<R: Runtime>(app: &AppHandle<R>, path: &str) {
    let _ = save(app, &load(app).with_recent(path));
}

pub fn clear_recent<R: Runtime>(app: &AppHandle<R>) {
    let _ = save(app, &Settings { recent: Vec::new(), ..load(app) });
}
```

`src/opens.rs`:

```rust
//! Paths to open that arrive from outside the page: Finder (Open With, a drop on the Dock icon, which can come
//! before the page is listening), File ▸ Open…, Open Recent, and drops on the window. Until the UI first asks
//! (`take_pending_opens`), they wait; after that each batch is sent as `open-paths`.
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, Runtime};

#[derive(Debug, Default)]
pub struct Opens {
    ready: bool,
    pending: Vec<String>,
}

impl Opens {
    /// Some(paths) to send now, or None when they are kept for later.
    pub fn offer(&mut self, paths: Vec<String>) -> Option<Vec<String>> {
        if self.ready {
            Some(paths)
        } else {
            self.pending.extend(paths);
            None
        }
    }

    /// What arrived before the UI was listening; from now on paths are sent as they come.
    pub fn take(&mut self) -> Vec<String> {
        self.ready = true;
        std::mem::take(&mut self.pending)
    }
}

pub fn deliver<R: Runtime>(app: &AppHandle<R>, paths: Vec<String>) {
    if paths.is_empty() {
        return;
    }
    let state = app.state::<Mutex<Opens>>();
    let now = state.lock().unwrap_or_else(std::sync::PoisonError::into_inner).offer(paths);
    if let Some(paths) = now {
        let _ = app.emit_to("main", "open-paths", paths);
    }
}
```

- [ ] **Step 4: Write `menu.rs`**

```rust
//! The menu bar. Each item the UI acts on is sent to it as a `menu` event with the item's id; File ▸ Open…,
//! Open Recent, Settings and Help are handled here. The UI reports what is possible (`set_menu_state`) and the
//! items follow.
use crate::{opens, settings};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Mutex;
use tauri::menu::{AboutMetadata, CheckMenuItem, CheckMenuItemBuilder, Menu, MenuItem, MenuItemBuilder, PredefinedMenuItem, Submenu, SubmenuBuilder};
use tauri::{AppHandle, Emitter, Manager, Runtime};

pub struct Entry {
    pub id: &'static str,
    pub label: &'static str,
    pub accel: Option<&'static str>,
    pub check: bool,
}

const fn e(id: &'static str, label: &'static str, accel: Option<&'static str>) -> Entry {
    Entry { id, label, accel, check: false }
}
const fn c(id: &'static str, label: &'static str, accel: Option<&'static str>) -> Entry {
    Entry { id, label, accel, check: true }
}

/// Every item of ours (the predefined ones, About, Hide, Copy and so on, are added in `build`).
pub const MENU: &[Entry] = &[
    e("settings", "Settings…", Some("CmdOrCtrl+,")),
    e("open", "Open…", Some("CmdOrCtrl+O")),
    e("export-svg", "Export SVG…", Some("CmdOrCtrl+E")),
    e("export-png-1", "1×…", None),
    e("export-png-2", "2×…", Some("Shift+CmdOrCtrl+E")),
    e("export-png-4", "4×…", None),
    e("export-all", "Export All…", Some("Alt+CmdOrCtrl+E")),
    e("reveal", "Show in Finder", Some("Alt+CmdOrCtrl+R")),
    e("copy-svg", "Copy SVG", Some("Shift+CmdOrCtrl+C")),
    e("zoom-in", "Zoom In", Some("CmdOrCtrl+=")),
    e("zoom-out", "Zoom Out", Some("CmdOrCtrl+-")),
    e("zoom-actual", "Actual Size", Some("CmdOrCtrl+0")),
    e("zoom-fit", "Zoom to Fit", Some("CmdOrCtrl+9")),
    c("mode-split", "Split", Some("CmdOrCtrl+1")),
    c("mode-side", "Side by Side", Some("CmdOrCtrl+2")),
    c("mode-overlay", "Overlay", Some("CmdOrCtrl+3")),
    c("mode-vector", "Vector Only", Some("CmdOrCtrl+4")),
    c("toggle-sidebar", "Show Sidebar", Some("Ctrl+Super+S")),
    c("toggle-inspector", "Show Inspector", Some("Alt+Super+I")),
    e("generate", "Generate Vector", Some("CmdOrCtrl+Enter")),
    e("cancel", "Cancel Trace", Some("CmdOrCtrl+.")),
    e("remove", "Remove Image", Some("CmdOrCtrl+Backspace")),
    e("clear", "Clear All", None),
    e("clear-recent", "Clear Menu", None),
    e("help", "Studi0Trace Help", None),
];

const HELP_URL: &str = "https://github.com/timothynice/tracer#readme";

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct MenuState {
    pub has_items: bool,
    pub has_image: bool,
    pub has_vector: bool,
    pub any_vector: bool,
    pub has_path: bool,
    pub tracing: bool,
    pub mode: String,
    pub sidebar: bool,
    pub inspector: bool,
}

/// For each item the state governs: (id, enabled, checked).
pub fn plan(s: &MenuState) -> Vec<(&'static str, bool, Option<bool>)> {
    let mut out = vec![
        ("export-svg", s.has_vector, None),
        ("export-png-1", s.has_vector, None),
        ("export-png-2", s.has_vector, None),
        ("export-png-4", s.has_vector, None),
        ("copy-svg", s.has_vector, None),
        ("export-all", s.any_vector, None),
        ("reveal", s.has_path, None),
        ("generate", s.has_image && !s.tracing, None),
        ("cancel", s.tracing, None),
        ("remove", s.has_image, None),
        ("clear", s.has_items, None),
        ("zoom-in", s.has_image, None),
        ("zoom-out", s.has_image, None),
        ("zoom-actual", s.has_image, None),
        ("zoom-fit", s.has_image, None),
        ("toggle-sidebar", true, Some(s.sidebar)),
        ("toggle-inspector", true, Some(s.inspector)),
    ];
    for (id, mode) in [("mode-split", "split"), ("mode-side", "side"), ("mode-overlay", "overlay"), ("mode-vector", "vector")] {
        out.push((id, s.has_image, Some(s.mode == mode)));
    }
    out
}

/// The items `plan` touches, and the Open Recent submenu, kept to change later.
pub struct Handles<R: Runtime> {
    items: HashMap<&'static str, MenuItem<R>>,
    checks: HashMap<&'static str, CheckMenuItem<R>>,
    recent: Submenu<R>,
}

fn entry(id: &str) -> &'static Entry {
    MENU.iter().find(|x| x.id == id).expect("a known menu id")
}

pub fn build<R: Runtime>(app: &AppHandle<R>, recent: &[String]) -> tauri::Result<(Menu<R>, Handles<R>)> {
    let mut items = HashMap::new();
    let mut checks = HashMap::new();
    let mut item = |id: &'static str| -> tauri::Result<MenuItem<R>> {
        let x = entry(id);
        let mut b = MenuItemBuilder::with_id(x.id, x.label);
        if let Some(a) = x.accel {
            b = b.accelerator(a);
        }
        let built = b.build(app)?;
        items.insert(x.id, built.clone());
        Ok(built)
    };
    let mut check = |id: &'static str| -> tauri::Result<CheckMenuItem<R>> {
        let x = entry(id);
        let mut b = CheckMenuItemBuilder::with_id(x.id, x.label).checked(false);
        if let Some(a) = x.accel {
            b = b.accelerator(a);
        }
        let built = b.build(app)?;
        checks.insert(x.id, built.clone());
        Ok(built)
    };

    let about = AboutMetadata {
        name: Some("Studi0Trace".into()),
        version: Some(env!("CARGO_PKG_VERSION").into()),
        copyright: Some("Copyright © 2026 Timothy Nice. MIT licensed.".into()),
        ..Default::default()
    };
    let app_menu = SubmenuBuilder::new(app, "Studi0Trace")
        .about(Some(about))
        .separator()
        .item(&item("settings")?)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .quit()
        .build()?;
    let recent_menu = SubmenuBuilder::with_id(app, "open-recent", "Open Recent").build()?;
    let png = SubmenuBuilder::new(app, "Export PNG").item(&item("export-png-1")?).item(&item("export-png-2")?).item(&item("export-png-4")?).build()?;
    let file = SubmenuBuilder::new(app, "File")
        .item(&item("open")?)
        .item(&recent_menu)
        .separator()
        .item(&item("export-svg")?)
        .item(&png)
        .item(&item("export-all")?)
        .separator()
        .item(&item("reveal")?)
        .separator()
        .close_window()
        .build()?;
    let edit = SubmenuBuilder::new(app, "Edit").undo().redo().separator().cut().copy().paste().select_all().separator().item(&item("copy-svg")?).build()?;
    let view = SubmenuBuilder::new(app, "View")
        .item(&item("zoom-in")?)
        .item(&item("zoom-out")?)
        .item(&item("zoom-actual")?)
        .item(&item("zoom-fit")?)
        .separator()
        .item(&check("mode-split")?)
        .item(&check("mode-side")?)
        .item(&check("mode-overlay")?)
        .item(&check("mode-vector")?)
        .separator()
        .item(&check("toggle-sidebar")?)
        .item(&check("toggle-inspector")?)
        .separator()
        .fullscreen()
        .build()?;
    let image = SubmenuBuilder::new(app, "Image")
        .item(&item("generate")?)
        .item(&item("cancel")?)
        .separator()
        .item(&item("remove")?)
        .item(&item("clear")?)
        .build()?;
    let window = SubmenuBuilder::new(app, "Window").minimize().maximize().build()?;
    let help = SubmenuBuilder::new(app, "Help").item(&item("help")?).build()?;
    let menu = Menu::with_items(app, &[&app_menu, &file, &edit, &view, &image, &window, &help])?;
    let handles = Handles { items, checks, recent: recent_menu };
    fill_recent(app, &handles.recent, recent)?;
    Ok((menu, handles))
}

fn fill_recent<R: Runtime>(app: &AppHandle<R>, submenu: &Submenu<R>, recent: &[String]) -> tauri::Result<()> {
    while !submenu.items()?.is_empty() {
        submenu.remove_at(0)?;
    }
    for (i, path) in recent.iter().enumerate() {
        let label = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.clone());
        submenu.append(&MenuItemBuilder::with_id(format!("recent-{i}"), label).build(app)?)?;
    }
    if !recent.is_empty() {
        submenu.append(&PredefinedMenuItem::separator(app)?)?;
    }
    submenu.append(&MenuItemBuilder::with_id("clear-recent", entry("clear-recent").label).enabled(!recent.is_empty()).build(app)?)?;
    Ok(())
}

pub fn set_recent<R: Runtime>(app: &AppHandle<R>, recent: &[String]) {
    if let Some(h) = app.try_state::<Mutex<Handles<R>>>() {
        let h = h.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        let _ = fill_recent(app, &h.recent, recent);
    }
}

pub fn apply_state<R: Runtime>(app: &AppHandle<R>, state: &MenuState) {
    let Some(h) = app.try_state::<Mutex<Handles<R>>>() else { return };
    let h = h.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    for (id, enabled, checked) in plan(state) {
        if let Some(i) = h.items.get(id) {
            let _ = i.set_enabled(enabled);
        }
        if let Some(c) = h.checks.get(id) {
            let _ = c.set_enabled(enabled);
            if let Some(on) = checked {
                let _ = c.set_checked(on);
            }
        }
    }
}

/// A menu item was chosen.
pub fn on_menu<R: Runtime>(app: &AppHandle<R>, id: &str) {
    match id {
        "open" => {
            let app = app.clone();
            tauri::async_runtime::spawn_blocking(move || {
                use tauri_plugin_dialog::DialogExt;
                let picked = app.dialog().file().set_title("Open Images").add_filter("Images", crate::commands::IMAGE_EXTENSIONS).blocking_pick_files().unwrap_or_default();
                let paths = picked.into_iter().filter_map(|f| f.into_path().ok()).map(|p| p.display().to_string()).collect();
                opens::deliver(&app, paths);
            });
        }
        "clear-recent" => settings::clear_recent(app),
        "settings" => {
            let _ = crate::commands::show_settings_window(app);
        }
        "help" => {
            use tauri_plugin_opener::OpenerExt;
            let _ = app.opener().open_url(HELP_URL, None::<&str>);
        }
        recent if recent.starts_with("recent-") => {
            let at: usize = recent["recent-".len()..].parse().unwrap_or(usize::MAX);
            if let Some(path) = settings::load(app).recent.get(at).cloned() {
                opens::deliver(app, vec![path]);
            }
        }
        other => {
            let _ = app.emit_to("main", "menu", serde_json::json!({ "id": other }));
        }
    }
}
```

- [ ] **Step 5: Add the commands, the recent-file note and the events**

In `src/commands.rs`, change `open_one` to note recent files, and add the five commands:

```rust
pub(crate) fn open_one<R: tauri::Runtime>(app: &tauri::AppHandle<R>, state: &AppState, path: PathBuf, downscale: bool) -> Outcome {
    match intake::open_path(&state.core, &path, downscale) {
        Ok(image) => {
            crate::settings::note_recent(app, &path.display().to_string());
            Outcome::Ok(state.images.insert(image))
        }
        Err(error) => Outcome::Failed {
            name: path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
            path: Some(path.display().to_string()),
            error,
        },
    }
}
```

(`open_paths` and `pick_images` take `app: tauri::AppHandle` and pass `&app`.)

```rust
use crate::menu::MenuState;
use crate::settings::{self, Settings};
use std::sync::Mutex;

#[tauri::command]
pub fn load_settings(app: tauri::AppHandle) -> Settings {
    settings::load(&app)
}

#[tauri::command]
pub async fn save_settings(app: tauri::AppHandle, settings: Settings) -> Result<Settings, CommandError> {
    let next = settings::load(&app).merged(settings);
    settings::save(&app, &next)?;
    Ok(next)
}

#[tauri::command]
pub fn set_menu_state(app: tauri::AppHandle, state: MenuState) {
    crate::menu::apply_state(&app, &state);
}

#[tauri::command]
pub fn take_pending_opens(opens: State<'_, Mutex<crate::opens::Opens>>) -> Vec<String> {
    opens.lock().unwrap_or_else(std::sync::PoisonError::into_inner).take()
}

pub(crate) fn show_settings_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> tauri::Result<()> {
    use tauri::Manager;
    if let Some(w) = app.get_webview_window("settings") {
        w.show()?;
        return w.set_focus();
    }
    tauri::WebviewWindowBuilder::new(app, "settings", tauri::WebviewUrl::App("index.html".into()))
        .title("Settings")
        .inner_size(520.0, 400.0)
        .resizable(false)
        .minimizable(false)
        .maximizable(false)
        .build()?;
    Ok(())
}

#[tauri::command]
pub fn open_settings_window(app: tauri::AppHandle) -> Result<(), CommandError> {
    show_settings_window(&app).map_err(|e| CommandError::new(500, "io_error", e.to_string()))
}
```

In `src/lib.rs`: add `pub mod menu; pub mod opens; pub mod settings;`; `.manage(std::sync::Mutex::new(opens::Opens::default()))`; add the five commands to the handler; and build the menu, route its events, and handle drops and Finder opens:

```rust
        .setup(|app| {
            let (menu, handles) = menu::build(app.handle(), &settings::load(app.handle()).recent)?;
            app.set_menu(menu)?;
            app.manage(std::sync::Mutex::new(handles));
            Ok(())
        })
        .on_menu_event(|app, event| menu::on_menu(app, event.id().as_ref()))
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::DragDrop(drop) = event {
                use tauri::{DragDropEvent, Emitter};
                match drop {
                    DragDropEvent::Enter { .. } => {
                        let _ = window.emit("drag-state", true);
                    }
                    DragDropEvent::Leave => {
                        let _ = window.emit("drag-state", false);
                    }
                    DragDropEvent::Drop { paths, .. } => {
                        let _ = window.emit("drag-state", false);
                        opens::deliver(window.app_handle(), paths.iter().map(|p| p.display().to_string()).collect());
                    }
                    _ => {}
                }
            }
        })
```

and in the `run` closure:

```rust
        .run(|app, event| match event {
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Opened { urls } => {
                opens::deliver(app, urls.into_iter().filter_map(|u| u.to_file_path().ok()).map(|p| p.display().to_string()).collect());
            }
            tauri::RunEvent::Exit => {
                use tauri::Manager;
                app.state::<AppState>().queue.shutdown();
            }
            _ => {}
        });
```

- [ ] **Step 6: Run the tests and the app**

Run: `cargo test -p studi0trace-desktop --release` and `cargo clippy -p studi0trace-desktop --release --all-targets`
Expected: PASS, no warnings. Then `cd apps/desktop && npx tauri build --debug --bundles app && open -n ../../target/debug/bundle/macos/Studi0Trace.app`: the menu bar reads Studi0Trace, File, Edit, View, Image, Window, Help; Studi0Trace ▸ Settings… opens a second window; quit with ⌘Q. (Check the menu with `osascript -e 'tell application "System Events" to tell process "Studi0Trace" to get name of every menu bar item of menu bar 1'` if the controller cannot look; expect `Apple, Studi0Trace, File, Edit, View, Image, Window, Help`. If System Events is not allowed to control the computer, say so in the report.)

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src-tauri
git commit -m "desktop: the menu bar, settings, Open Recent, and files from Finder, the Dock and drops

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The frontend's platform layer

One interface the UI calls for everything that differs between the Mac app and a browser. `native` invokes the commands of Tasks 2–5; `web` keeps the existing HTTP client to the Python server and browser fallbacks, so `npm run dev` in a browser stays a working harness.

**Files:**
- Create: `frontend/src/platform/types.ts`, `web.ts`, `native.ts`, `index.ts`
- Modify: `frontend/src/lib/api.ts` (extract `apiErrorFromBody`), `frontend/package.json` (`@tauri-apps/api`), `frontend/src/test/setup.ts` (Node 26)
- Test: `frontend/src/platform/web.test.ts`, `frontend/src/platform/native.test.ts`

**Interfaces:**
- Consumes: the Rust commands and events of Tasks 2–5 (names, arguments and payloads as listed there).
- Produces (`@/platform/types`, re-exported by `@/platform`):

```ts
export interface OpenedImage { id: string; name: string; path: string | null; width: number; height: number; format: string; previewUrl: string }
export interface OpenFailure { name: string; path: string | null; error: ApiError }
export type OpenOutcome = { ok: OpenedImage } | { failed: OpenFailure };
export type Phase = "queued" | "tracing";
export interface TraceRequest { imageId: string; parameters: ParamValues; auto: boolean; job: string }
export interface TraceOptions { signal: AbortSignal; onPhase?: (phase: Phase) => void }
export interface Settings { appearance: "system" | "light" | "dark"; exportTo: "ask" | "beside"; revealAfterExport: boolean; traceOnOpen: boolean; liveUpdate: boolean; recent: string[] }
export const DEFAULT_SETTINGS: Settings;
export type ViewMode = "split" | "side" | "overlay" | "vector";
export type MenuCommand = "export-svg" | "export-png-1" | "export-png-2" | "export-png-4" | "export-all" | "reveal" | "copy-svg" | "zoom-in" | "zoom-out" | "zoom-actual" | "zoom-fit" | "mode-split" | "mode-side" | "mode-overlay" | "mode-vector" | "toggle-sidebar" | "toggle-inspector" | "generate" | "cancel" | "remove" | "clear";
export interface MenuState { hasItems: boolean; hasImage: boolean; hasVector: boolean; anyVector: boolean; hasPath: boolean; tracing: boolean; mode: ViewMode; sidebar: boolean; inspector: boolean }
export interface ExportFile { kind: "svg" | "png"; imageId: string; name: string; bytes: Uint8Array<ArrayBuffer> }
export interface Platform { /* see Step 3 */ }
```

  and `platform` (`@/platform`), `webPlatform()`, `nativePlatform()`, `apiErrorFromBody(status, body, statusText?)` (`@/lib/api`).

- [ ] **Step 1: Make the suite pass on the Node in use**

Node 25 and later define a global `localStorage` of their own, which shadows jsdom's and fails 11 tests on Node 26. At the top of `frontend/src/test/setup.ts`, after the imports:

```ts
// Node 25+ has a `localStorage` global of its own (backed by --localstorage-file, and useless without it). It
// shadows jsdom's on globalThis; the tests mean jsdom's.
const jsdomStorage = (globalThis as { jsdom?: { window: Window } }).jsdom?.window.localStorage ?? window.localStorage;
if (globalThis.localStorage !== jsdomStorage) {
  Object.defineProperty(globalThis, "localStorage", { value: jsdomStorage, configurable: true, writable: true });
}
```

Run: `cd frontend && npm run test:run`
Expected: 73 passed on Node 26 with no `NODE_OPTIONS` (tried while writing this plan: vitest's jsdom environment exposes `globalThis.jsdom`, Node's `localStorage` is a getter on `globalThis`, and replacing it this way passes all 73).

- [ ] **Step 2: Write the failing tests**

`frontend/src/platform/web.test.ts`:

```ts
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { API_URL } from "@/lib/api";
import { server } from "@/test/server";
import { DEFAULT_SETTINGS } from "./types";
import { webPlatform } from "./web";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

const png = (name = "a.png") => new File([new Uint8Array([137, 80, 78, 71, 1, 2, 3])], name, { type: "image/png" });

describe("web platform", () => {
  it("uploads a file and names it by its hash, not the server's id", async () => {
    server.use(http.post(`${API_URL}/uploads`, () => HttpResponse.json({ image_id: "srv-1", width: 64, height: 32, format: "PNG" })));
    const [o] = await webPlatform().openFiles([png()]);
    if (!("ok" in o)) throw new Error("expected ok");
    expect(o.ok).toMatchObject({ name: "a.png", path: null, width: 64, height: 32, format: "PNG" });
    expect(o.ok.id).not.toBe("srv-1");
    expect(o.ok.previewUrl).toMatch(/^blob:/);
  });

  it("reports a refused upload with the server's code and words", async () => {
    server.use(http.post(`${API_URL}/uploads`, () => HttpResponse.json({ detail: { code: "too_many_pixels", message: "Image exceeds the 2048x2048 pixel limit" } }, { status: 400 })));
    const [o] = await webPlatform().openFiles([png()]);
    if (!("failed" in o)) throw new Error("expected a failure");
    expect([o.failed.error.code, o.failed.error.message]).toEqual(["too_many_pixels", "Image exceeds the 2048x2048 pixel limit"]);
  });

  it("traces with the vexel parameters and uploads again when the server has forgotten the image", async () => {
    let uploads = 0;
    const forms: FormData[] = [];
    server.use(
      http.post(`${API_URL}/uploads`, () => HttpResponse.json({ image_id: `srv-${++uploads}`, width: 4, height: 4, format: "PNG" })),
      http.post(`${API_URL}/vectorize`, async ({ request }) => {
        const form = await request.formData();
        forms.push(form);
        if (form.get("image_id") === "srv-1") return HttpResponse.json({ detail: { code: "image_expired", message: "gone" } }, { status: 404 });
        return HttpResponse.json({ success: true, image_id: "srv-2", width: 4, height: 4, results: { vexel: { svg: "<svg/>", elapsed_ms: 1, stats: {} } }, parameters_used: { vexel: {} } });
      }),
    );
    const p = webPlatform();
    const [o] = await p.openFiles([png()]);
    if (!("ok" in o)) throw new Error("expected ok");
    const res = await p.vectorize({ imageId: o.ok.id, parameters: { detail: 7 }, auto: false, job: "j" }, { signal: new AbortController().signal });
    expect(res.results.vexel.svg).toBe("<svg/>");
    expect(uploads).toBe(2);
    expect(forms[1].get("engines")).toBe("vexel");
    expect(JSON.parse(String(forms[1].get("parameters")))).toEqual({ vexel: { detail: 7 } });
  });

  it("keeps settings in localStorage, with no recent list", async () => {
    const p = webPlatform();
    const seen: unknown[] = [];
    p.onSettings((s) => seen.push(s));
    const saved = await p.saveSettings({ ...DEFAULT_SETTINGS, appearance: "dark", recent: ["/x.png"] });
    expect(saved.recent).toEqual([]);
    expect((await webPlatform().loadSettings()).appearance).toBe("dark");
    expect(seen).toHaveLength(1);
  });

  it("has no paths, no menu and no settings window", async () => {
    const p = webPlatform();
    await expect(p.openPaths(["/x.png"])).rejects.toMatchObject({ code: "unsupported" });
    expect(p.openSettingsWindow()).toBe(false);
    expect(p.windowRole()).toBe("main");
  });
});
```

`frontend/src/platform/native.test.ts`:

```ts
import { clearMocks, mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import { emit } from "@tauri-apps/api/event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { nativePlatform } from "./native";
import { DEFAULT_SETTINGS } from "./types";

type Handler = (cmd: string, payload: unknown) => unknown;
let calls: { cmd: string; payload: unknown }[] = [];

function ipc(handler: Handler) {
  calls = [];
  mockIPC(
    (cmd, payload) => {
      calls.push({ cmd, payload });
      return handler(cmd, payload);
    },
    { shouldMockEvents: true },
  );
}

beforeEach(() => {
  (window as unknown as { isTauri: boolean }).isTauri = true;
  mockWindows("main");
});
afterEach(() => clearMocks());

const opened = { id: "f".repeat(32), name: "logo.png", path: "/pics/logo.png", width: 64, height: 32, format: "PNG" };

describe("native platform", () => {
  it("opens paths, reads each preview once and keeps refusals with their code", async () => {
    ipc((cmd) => {
      if (cmd === "open_paths") return [{ ok: opened }, { failed: { name: "huge.png", path: "/pics/huge.png", error: { status: 400, body: { detail: { code: "too_many_pixels", message: "too big" } } } } }];
      if (cmd === "read_image") return new Uint8Array([1, 2, 3]).buffer;
      throw new Error(`unexpected ${cmd}`);
    });
    const p = nativePlatform();
    const [ok, failed] = await p.openPaths(["/pics/logo.png", "/pics/huge.png"], { downscale: false });
    if (!("ok" in ok) || !("failed" in failed)) throw new Error("shapes");
    expect(ok.ok).toMatchObject({ ...opened, previewUrl: expect.stringMatching(/^blob:/) });
    expect(failed.failed.error.code).toBe("too_many_pixels");
    expect(calls[0]).toEqual({ cmd: "open_paths", payload: { paths: ["/pics/logo.png", "/pics/huge.png"], downscale: false } });
    await p.openPaths(["/pics/logo.png"]);
    expect(calls.filter((c) => c.cmd === "read_image")).toHaveLength(1);
  });

  it("turns a command error into the UI's ApiError, the 422 list included", async () => {
    ipc((cmd) => {
      if (cmd === "vectorize") throw { status: 422, body: { detail: [{ loc: ["vexel", "detail"], msg: "Input should be a valid number" }] } };
      return null;
    });
    const p = nativePlatform();
    await expect(p.vectorize({ imageId: "i", parameters: { detail: "x" }, auto: false, job: "j1" }, { signal: new AbortController().signal })).rejects.toMatchObject({
      code: "validation_error",
      message: "vexel.detail: Input should be a valid number",
      status: 422,
    });
  });

  it("cancels the job when the signal aborts, and passes its phase on", async () => {
    let finish: (v: unknown) => void = () => {};
    ipc((cmd) => {
      if (cmd === "vectorize") return new Promise((r) => (finish = r));
      if (cmd === "cancel_trace") return true;
      return null;
    });
    const p = nativePlatform();
    const phases: string[] = [];
    const ctl = new AbortController();
    const done = p.vectorize({ imageId: "i", parameters: {}, auto: true, job: "j2" }, { signal: ctl.signal, onPhase: (ph) => phases.push(ph) });
    await new Promise((r) => setTimeout(r, 0));
    await emit("trace-phase", { job: "other", phase: "tracing" });
    await emit("trace-phase", { job: "j2", phase: "tracing" });
    ctl.abort();
    finish({ success: true });
    await done;
    expect(phases).toEqual(["tracing"]);
    expect(calls.find((c) => c.cmd === "cancel_trace")?.payload).toEqual({ job: "j2" });
    expect(calls.find((c) => c.cmd === "vectorize")?.payload).toEqual({ imageId: "i", parameters: {}, auto: true, job: "j2" });
  });

  it("sends an export's bytes and returns the written path", async () => {
    ipc((cmd) => (cmd === "export_file" ? "/pics/logo.svg" : null));
    const bytes = new TextEncoder().encode("<svg/>");
    const path = await nativePlatform().exportFile({ kind: "svg", imageId: "i", name: "logo.svg", bytes }, DEFAULT_SETTINGS);
    expect(path).toBe("/pics/logo.svg");
    expect(calls[0].payload).toEqual(bytes);
  });

  it("hands over paths that arrived before the page listened, then new ones", async () => {
    ipc((cmd) => (cmd === "take_pending_opens" ? ["/early.png"] : null));
    const got: string[][] = [];
    const off = nativePlatform().onOpenPaths((paths) => got.push(paths));
    await new Promise((r) => setTimeout(r, 0));
    await emit("open-paths", ["/later.png"]);
    off();
    await emit("open-paths", ["/after-off.png"]);
    expect(got).toEqual([["/early.png"], ["/later.png"]]);
  });

  it("routes menu items and settings changes", async () => {
    ipc(() => null);
    const p = nativePlatform();
    const menu: string[] = [];
    const settings: unknown[] = [];
    p.onMenu((id) => menu.push(id));
    p.onSettings((s) => settings.push(s));
    await new Promise((r) => setTimeout(r, 0));
    await emit("menu", { id: "zoom-fit" });
    await emit("settings-changed", { ...DEFAULT_SETTINGS, appearance: "dark" });
    expect(menu).toEqual(["zoom-fit"]);
    expect(settings).toEqual([{ ...DEFAULT_SETTINGS, appearance: "dark" }]);
    expect(p.windowRole()).toBe("main");
  });
});
```

Run: `cd frontend && npm install @tauri-apps/api@^2.12.1 && npx vitest run src/platform`
Expected: FAIL (`./web`, `./native` do not exist).

- [ ] **Step 3: Write the platform layer**

In `frontend/src/lib/api.ts`, replace `toApiError` with:

```ts
/**
 * The UI's error for a failed answer, whoever sent it: the Python server's HTTP body, or the Mac app's command
 * error, which carries the same `{"detail": …}` (a `{code, message}`, or the 422's list).
 */
export function apiErrorFromBody(status: number, body: unknown, statusText = ""): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    const where = first?.loc?.join(".") ?? "";
    return new ApiError("validation_error", first?.msg ? `${where}: ${first.msg}` : "Invalid parameters", status, detail);
  }
  if (detail && typeof detail === "object" && "code" in detail) {
    const d = detail as { code: string; message?: string };
    return new ApiError(d.code, d.message ?? d.code, status, detail);
  }
  if (status >= 500) {
    return new ApiError(`http_${status}`, `The server didn't finish the trace (${status}). Large or highly detailed images can exhaust it — try a smaller image.`, status, body);
  }
  return new ApiError(`http_${status}`, typeof detail === "string" ? detail : statusText || "Request failed", status, body);
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }
  return apiErrorFromBody(res.status, body, res.statusText);
}
```

`frontend/src/platform/types.ts`:

```ts
/** What differs between the Mac app and a browser, behind one interface the UI calls. */
import type { ApiError, EngineDescription, Health, ParamValues, Preset, VectorizeResponse } from "@/lib/api";

export interface OpenedImage {
  /** The image's id (a hash of the file): the same file opened twice is one image. */
  id: string;
  name: string;
  /** Where the file is; null for samples, pastes and everything in a browser. */
  path: string | null;
  width: number;
  height: number;
  format: string;
  /** A blob: URL of the source, owned by the platform until `closeImage`. */
  previewUrl: string;
}

export interface OpenFailure {
  name: string;
  path: string | null;
  error: ApiError;
}

export type OpenOutcome = { ok: OpenedImage } | { failed: OpenFailure };

export type Phase = "queued" | "tracing";

export interface TraceRequest {
  imageId: string;
  /** The vexel values; ignored when `auto`. */
  parameters: ParamValues;
  auto: boolean;
  /** Names the job, so it can be cancelled and its phase reported. */
  job: string;
}

export interface TraceOptions {
  signal: AbortSignal;
  onPhase?: (phase: Phase) => void;
}

export interface Settings {
  appearance: "system" | "light" | "dark";
  exportTo: "ask" | "beside";
  revealAfterExport: boolean;
  traceOnOpen: boolean;
  liveUpdate: boolean;
  /** Paths, most recent first; only the app changes it. */
  recent: string[];
}

export const DEFAULT_SETTINGS: Settings = { appearance: "system", exportTo: "ask", revealAfterExport: false, traceOnOpen: false, liveUpdate: true, recent: [] };

export type ViewMode = "split" | "side" | "overlay" | "vector";

export type MenuCommand =
  | "export-svg"
  | "export-png-1"
  | "export-png-2"
  | "export-png-4"
  | "export-all"
  | "reveal"
  | "copy-svg"
  | "zoom-in"
  | "zoom-out"
  | "zoom-actual"
  | "zoom-fit"
  | "mode-split"
  | "mode-side"
  | "mode-overlay"
  | "mode-vector"
  | "toggle-sidebar"
  | "toggle-inspector"
  | "generate"
  | "cancel"
  | "remove"
  | "clear";

export interface MenuState {
  hasItems: boolean;
  hasImage: boolean;
  hasVector: boolean;
  anyVector: boolean;
  hasPath: boolean;
  tracing: boolean;
  mode: ViewMode;
  sidebar: boolean;
  inspector: boolean;
}

export interface ExportFile {
  kind: "svg" | "png";
  imageId: string;
  /** The suggested file name, e.g. "logo.svg" or "logo@2x.png". */
  name: string;
  bytes: Uint8Array<ArrayBuffer>;
}

export interface Platform {
  readonly kind: "native" | "web";
  health(signal?: AbortSignal): Promise<Health>;
  engines(signal?: AbortSignal): Promise<EngineDescription[]>;
  presets(signal?: AbortSignal): Promise<Preset[]>;
  /** The open panel (a file picker in a browser), and what was chosen, opened. */
  pickImages(): Promise<OpenOutcome[]>;
  /** Files with no path: samples, pastes, browser drops. */
  openFiles(files: File[]): Promise<OpenOutcome[]>;
  /** Paths the app was given (drops, Open With, recent files); `downscale` fits them to the side cap. */
  openPaths(paths: string[], opts?: { downscale?: boolean }): Promise<OpenOutcome[]>;
  closeImage(id: string): void;
  vectorize(req: TraceRequest, opts: TraceOptions): Promise<VectorizeResponse>;
  /** The path written; null when the save panel was cancelled. In a browser the file is downloaded and this is its name. */
  exportFile(file: ExportFile, settings: Settings): Promise<string | null>;
  exportAll(files: { name: string; svg: string }[], settings: Settings): Promise<string[] | null>;
  copyText(text: string): Promise<void>;
  reveal(path: string): Promise<void>;
  loadSettings(): Promise<Settings>;
  saveSettings(settings: Settings): Promise<Settings>;
  onSettings(cb: (settings: Settings) => void): () => void;
  onMenu(cb: (command: MenuCommand) => void): () => void;
  /** Paths to open from outside the page; the ones that came before the page listened are handed over first. */
  onOpenPaths(cb: (paths: string[]) => void): () => void;
  /** Files are being dragged over the window (true) or no longer are (false). Native only. */
  onDragState(cb: (over: boolean) => void): () => void;
  setMenuState(state: MenuState): void;
  /** Settings in a window of its own; false where there is none (a browser shows its own sheet). */
  openSettingsWindow(): boolean;
  windowRole(): "main" | "settings";
}
```

`frontend/src/platform/web.ts`:

```ts
/** The browser: the Python server over HTTP, a file picker, downloads and localStorage. A development harness. */
import { ApiError, getEngines, getHealth, getPresets, uploadImage, vectorize as httpVectorize } from "@/lib/api";
import { hashFile } from "@/lib/hash";
import { downloadBlob } from "@/lib/raster";
import { DEFAULT_SETTINGS, type OpenOutcome, type Platform, type Settings } from "./types";

const SETTINGS_KEY = "studi0trace.settings";
export const ACCEPTED = "image/png,image/jpeg,image/gif,image/webp,image/bmp";

function readSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    return { ...DEFAULT_SETTINGS, ...(raw ? (JSON.parse(raw) as Partial<Settings>) : {}), recent: [] };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function webPlatform(): Platform {
  // the server's id for each image, and the file, to send again if the server has forgotten it
  const uploads = new Map<string, { file: File; serverId: string; url: string }>();
  const settingsListeners = new Set<(s: Settings) => void>();

  async function openFiles(files: File[]): Promise<OpenOutcome[]> {
    const out: OpenOutcome[] = [];
    for (const file of files) {
      try {
        const id = await hashFile(file);
        const up = await uploadImage(file);
        const url = uploads.get(id)?.url ?? URL.createObjectURL(file);
        uploads.set(id, { file, serverId: up.image_id, url });
        out.push({ ok: { id, name: file.name, path: null, width: up.width, height: up.height, format: up.format, previewUrl: url } });
      } catch (err) {
        out.push({ failed: { name: file.name, path: null, error: err instanceof ApiError ? err : new ApiError("network", String(err)) } });
      }
    }
    return out;
  }

  return {
    kind: "web",
    health: (signal) => getHealth(signal),
    engines: (signal) => getEngines(signal),
    presets: (signal) => getPresets(signal),
    pickImages: () =>
      new Promise((resolve) => {
        const input = document.createElement("input");
        input.type = "file";
        input.multiple = true;
        input.accept = ACCEPTED;
        input.onchange = () => void openFiles(Array.from(input.files ?? [])).then(resolve);
        input.oncancel = () => resolve([]);
        input.click();
      }),
    openFiles,
    openPaths: async () => {
      throw new ApiError("unsupported", "Opening files by path needs the Mac app");
    },
    closeImage(id) {
      const u = uploads.get(id);
      if (u) URL.revokeObjectURL(u.url);
      uploads.delete(id);
    },
    async vectorize(req, { signal }) {
      const entry = uploads.get(req.imageId);
      if (!entry) throw new ApiError("image_expired", "Upload expired or unknown; upload it again", 404);
      const send = (serverId: string) => httpVectorize({ imageId: serverId, engines: ["vexel"], parameters: { vexel: req.parameters }, auto: req.auto }, signal);
      try {
        return await send(entry.serverId);
      } catch (err) {
        if (!(err instanceof ApiError) || err.code !== "image_expired") throw err;
        const up = await uploadImage(entry.file, signal);
        entry.serverId = up.image_id;
        return send(up.image_id);
      }
    },
    async exportFile(file) {
      downloadBlob(new Blob([file.bytes], { type: file.kind === "png" ? "image/png" : "image/svg+xml" }), file.name);
      return file.name;
    },
    async exportAll(files) {
      for (const f of files) downloadBlob(new Blob([f.svg], { type: "image/svg+xml" }), f.name);
      return files.map((f) => f.name);
    },
    copyText: (text) => navigator.clipboard.writeText(text),
    reveal: async () => {},
    loadSettings: async () => readSettings(),
    async saveSettings(settings) {
      const next = { ...settings, recent: [] };
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
      } catch {
        /* private mode: settings last for the session */
      }
      settingsListeners.forEach((l) => l(next));
      return next;
    },
    onSettings(cb) {
      settingsListeners.add(cb);
      return () => settingsListeners.delete(cb);
    },
    onMenu: () => () => {},
    onOpenPaths: () => () => {},
    onDragState: () => () => {},
    setMenuState: () => {},
    openSettingsWindow: () => false,
    windowRole: () => "main",
  };
}
```

`frontend/src/platform/native.ts`:

```ts
/** The Mac app: Tauri commands (Tasks 2–5 of plan 2) and the events the app sends. */
import { invoke, type InvokeArgs } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { ApiError, apiErrorFromBody, type EngineDescription, type Health, type Preset, type VectorizeResponse } from "@/lib/api";
import { DEFAULT_SETTINGS, type MenuCommand, type OpenOutcome, type Platform, type Settings } from "./types";

interface CommandError {
  status: number;
  body: unknown;
}
interface OpenedDto {
  id: string;
  name: string;
  path: string | null;
  width: number;
  height: number;
  format: string;
}
type OutcomeDto = { ok: OpenedDto } | { failed: { name: string; path: string | null; error: CommandError } };

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (err && typeof err === "object" && "status" in err && "body" in err) {
    const e = err as CommandError;
    return apiErrorFromBody(e.status, e.body);
  }
  return new ApiError("io_error", typeof err === "string" ? err : ((err as Error | null)?.message ?? "The app could not do that"));
}

async function call<T>(cmd: string, args?: InvokeArgs, headers?: Record<string, string>): Promise<T> {
  try {
    return await invoke<T>(cmd, args, headers ? { headers } : undefined);
  } catch (err) {
    throw toApiError(err);
  }
}

/** Listen to an app event; the returned function stops listening, even before `listen` has resolved. */
function on<T>(event: string, cb: (payload: T) => void): () => void {
  let off: UnlistenFn | null = null;
  let done = false;
  void listen<T>(event, (e) => cb(e.payload)).then((f) => {
    if (done) f();
    else off = f;
  });
  return () => {
    done = true;
    off?.();
  };
}

// Paths taken from the app after their listener had gone (React StrictMode mounts, unmounts and mounts again):
// the next listener gets them.
let unclaimed: string[] = [];

export function nativePlatform(): Platform {
  const previews = new Map<string, string>();

  async function outcomes(dtos: OutcomeDto[]): Promise<OpenOutcome[]> {
    return Promise.all(
      dtos.map(async (d): Promise<OpenOutcome> => {
        if ("failed" in d) return { failed: { name: d.failed.name, path: d.failed.path, error: toApiError(d.failed.error) } };
        let url = previews.get(d.ok.id);
        if (!url) {
          const buf = await call<ArrayBuffer>("read_image", { id: d.ok.id });
          url = URL.createObjectURL(new Blob([buf]));
          previews.set(d.ok.id, url);
        }
        return { ok: { ...d.ok, previewUrl: url } };
      }),
    );
  }

  return {
    kind: "native",
    health: () => call<Health>("health"),
    engines: () => call<EngineDescription[]>("engines"),
    presets: () => call<Preset[]>("presets"),
    pickImages: async () => outcomes(await call<OutcomeDto[]>("pick_images")),
    openFiles: async (files) =>
      outcomes(await Promise.all(files.map(async (f) => call<OutcomeDto>("open_bytes", new Uint8Array(await f.arrayBuffer()), { "x-name": encodeURIComponent(f.name) })))),
    openPaths: async (paths, opts) => outcomes(await call<OutcomeDto[]>("open_paths", { paths, downscale: !!opts?.downscale })),
    closeImage(id) {
      const url = previews.get(id);
      if (url) URL.revokeObjectURL(url);
      previews.delete(id);
      void invoke("close_image", { id }).catch(() => {});
    },
    async vectorize(req, { signal, onPhase }) {
      if (signal.aborted) throw new ApiError("cancelled", "The trace was cancelled", 499);
      const offPhase = on<{ job: string; phase: "tracing" }>("trace-phase", (p) => {
        if (p.job === req.job) onPhase?.(p.phase);
      });
      const abort = () => void invoke("cancel_trace", { job: req.job }).catch(() => {});
      signal.addEventListener("abort", abort, { once: true });
      try {
        return await call<VectorizeResponse>("vectorize", { imageId: req.imageId, parameters: req.parameters, auto: req.auto, job: req.job });
      } finally {
        offPhase();
        signal.removeEventListener("abort", abort);
      }
    },
    exportFile: (file, settings) =>
      call<string | null>("export_file", file.bytes, {
        "x-kind": file.kind,
        "x-name": encodeURIComponent(file.name),
        "x-image": file.imageId,
        "x-destination": settings.exportTo,
        "x-reveal": settings.revealAfterExport ? "1" : "0",
      }),
    exportAll: (files, settings) => call<string[] | null>("export_all", { items: files, reveal: settings.revealAfterExport }),
    copyText: (text) => call<void>("copy_text", { text }),
    reveal: (path) => call<void>("reveal", { path }),
    loadSettings: async () => ({ ...DEFAULT_SETTINGS, ...(await call<Partial<Settings>>("load_settings")) }),
    saveSettings: (settings) => call<Settings>("save_settings", { settings }),
    onSettings: (cb) => on<Settings>("settings-changed", cb),
    onMenu: (cb) => on<{ id: MenuCommand }>("menu", (p) => cb(p.id)),
    onOpenPaths(cb) {
      let off: UnlistenFn | null = null;
      let done = false;
      if (unclaimed.length) cb(unclaimed.splice(0));
      void listen<string[]>("open-paths", (e) => cb(e.payload)).then((f) => {
        if (done) {
          f();
          return;
        }
        off = f;
        void call<string[]>("take_pending_opens").then((pending) => {
          if (!pending.length) return;
          if (done) unclaimed.push(...pending);
          else cb(pending);
        });
      });
      return () => {
        done = true;
        off?.();
      };
    },
    onDragState: (cb) => on<boolean>("drag-state", cb),
    setMenuState: (state) => void invoke("set_menu_state", { state }).catch(() => {}),
    openSettingsWindow: () => {
      void invoke("open_settings_window").catch(() => {});
      return true;
    },
    windowRole: () => (getCurrentWindow().label === "settings" ? "settings" : "main"),
  };
}
```

`frontend/src/platform/index.ts`:

```ts
import { isTauri } from "@tauri-apps/api/core";

import { nativePlatform } from "./native";
import type { Platform } from "./types";
import { webPlatform } from "./web";

export * from "./types";

/** The platform this page runs on, chosen once. */
export const platform: Platform = isTauri() ? nativePlatform() : webPlatform();
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd frontend && npx vitest run src/platform && npm run test:run && npm run typecheck`
Expected: the platform tests pass (11), the rest of the suite still passes, no type errors. (If `mockIPC` hands a raw-bytes payload over as something other than the `Uint8Array` given, compare `Array.from` of both instead; the shape of the test stays.)

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/platform frontend/src/lib/api.ts frontend/src/test/setup.ts
git commit -m "frontend: the platform layer, native over Tauri and web over the server; the suite passes on Node 26

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The library store

The images in the sidebar, each with its own settings, every trace seen for it, and the job
running for it; the live-update rule; Auto's candidates kept as their presets' traces. No
React in it: a store with `subscribe`, read by `useSyncExternalStore`, so it is tested as
plain functions.

**Files:**
- Create: `frontend/src/state/library.ts`, `frontend/src/state/useLibrary.ts`
- Test: `frontend/src/state/library.test.ts`

**Interfaces:**
- Consumes: `Platform`, `OpenOutcome`, `OpenFailure`, `OpenedImage`, `Settings`, `TraceRequest`, `Phase` (Task 6, `@/platform/types`); `ApiError`, `AutoResult`, `EngineDescription`, `ParamValues`, `Preset`, `VectorizeResponse` (`@/lib/api`); `normalizeValues`, `paramsKey`, `specsFor` (`@/lib/schema`).
- Produces (used by Tasks 8–13):
  - `ENGINE = "vexel"`, `LIVE_MS = 2000`, `DEBOUNCE_MS = 250`
  - `interface TraceAnswer { svg: string; elapsedMs: number; stats: Record<string, number> }`
  - `interface Job { id: string; key: string; startedAt: number; phase: Phase }`
  - `interface ImageItem { image: OpenedImage; preset: string | null; params: ParamValues; traces: Record<string, TraceAnswer>; shown: string | null; job: Job | null; error: ApiError | null; auto: AutoResult | null }`
  - `interface LibraryState { items: ImageItem[]; failed: OpenFailure[]; selected: string | null }`
  - `interface Catalog { engine: EngineDescription; presets: Preset[] }`
  - `traceKey(item): string`, `needsUpdate(item): boolean`, `shownAnswer(item): TraceAnswer | null`
  - `createLibrary(platform, catalog, settings, opts?): Library` with `getState, subscribe, add, select, selectNext, remove, clear, dismissFailure, downscale, pickPreset, setParam, generate, cancel, setSettings, selectedItem`
  - `useLibrary(lib): LibraryState` and `useSelected(lib): ImageItem | null`

- [ ] **Step 1: Write the failing tests**

`frontend/src/state/library.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type EngineDescription, type Preset, type VectorizeResponse } from "@/lib/api";
import { paramsKey } from "@/lib/schema";
import type { OpenOutcome, Phase, Platform, TraceRequest } from "@/platform/types";
import { DEFAULT_SETTINGS } from "@/platform/types";
import { createLibrary, DEBOUNCE_MS, needsUpdate, traceKey, type Catalog } from "./library";

const ENGINE: EngineDescription = {
  id: "vexel",
  label: "Vexel",
  description: "",
  primary: true,
  params: {
    properties: {
      detail: { type: "number", default: 6, minimum: 1, maximum: 20 },
      min_region: { type: "integer", default: 8, minimum: 1, maximum: 64 },
    },
  },
  defaults: { detail: 6, min_region: 8 },
};
const PRESETS: Preset[] = [
  { id: "auto", label: "Auto", engine: "vexel", description: "", detail: "", sample: "auto.png", params: {}, kind: "auto" },
  { id: "balanced", label: "Balanced", engine: "vexel", description: "", detail: "", sample: "b.png", params: {}, auto_candidate: true },
  { id: "logo", label: "Logo & icon", engine: "vexel", description: "", detail: "", sample: "l.png", params: { detail: 10, min_region: 16 }, auto_candidate: true },
];
const CATALOG: Catalog = { engine: ENGINE, presets: PRESETS };

const image = (id: string, path: string | null = `/pics/${id}.png`) => ({ id, name: `${id}.png`, path, width: 64, height: 32, format: "PNG", previewUrl: `blob:${id}` });
const ok = (id: string, path?: string | null): OpenOutcome => ({ ok: image(id, path) });

function response(svg: string, elapsed: number, extra: Partial<VectorizeResponse> = {}): VectorizeResponse {
  return {
    success: true,
    image_id: "x",
    width: 64,
    height: 32,
    results: { vexel: { svg, elapsed_ms: elapsed, stats: { paths: 3 } } },
    parameters_used: { vexel: {} },
    auto: null,
    ...extra,
  };
}

interface Call { req: TraceRequest; signal: AbortSignal; onPhase?: (p: Phase) => void; resolve: (r: VectorizeResponse) => void; reject: (e: unknown) => void }

function fakePlatform() {
  const calls: Call[] = [];
  const platform = {
    vectorize: vi.fn((req: TraceRequest, opts: { signal: AbortSignal; onPhase?: (p: Phase) => void }) =>
      new Promise<VectorizeResponse>((resolve, reject) => calls.push({ req, signal: opts.signal, onPhase: opts.onPhase, resolve, reject })),
    ),
    closeImage: vi.fn(),
    openPaths: vi.fn(async (paths: string[]) => paths.map((p) => ok("small", p))),
  } satisfies Pick<Platform, "vectorize" | "closeImage" | "openPaths">;
  return { platform, calls };
}

// the timers are fake; a few turns of the microtask queue let a settled promise's handlers run
const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};

describe("library", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] }));
  afterEach(() => vi.useRealTimers());

  it("opens images on Auto with the engine's defaults, selects the last, and does not trace", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    const s = lib.getState();
    expect(s.items.map((i) => i.image.id)).toEqual(["a", "b"]);
    expect(s.selected).toBe("b");
    expect(s.items[0]).toMatchObject({ preset: "auto", params: { detail: 6, min_region: 8 }, shown: null, job: null });
    expect(platform.vectorize).not.toHaveBeenCalled();
  });

  it("keeps one card per file and keeps the files it could not open", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    const failure = { failed: { name: "huge.png", path: "/pics/huge.png", error: new ApiError("too_many_pixels", "Image exceeds the 2048x2048 pixel limit", 400) } };
    lib.add([ok("a"), ok("a"), failure]);
    expect(lib.getState().items).toHaveLength(1);
    expect(lib.getState().failed.map((f) => f.name)).toEqual(["huge.png"]);
  });

  it("traces Auto, keeps every candidate as its preset's trace, and follows the pick", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a")]);
    lib.generate("a");
    expect(calls[0].req).toMatchObject({ imageId: "a", auto: true, parameters: {} });
    expect(lib.getState().items[0].job).toMatchObject({ key: "auto", phase: "queued" });
    calls[0].onPhase?.("tracing");
    expect(lib.getState().items[0].job?.phase).toBe("tracing");
    calls[0].resolve(
      response("<svg>logo</svg>", 900, {
        auto: {
          vexel: {
            engine: "vexel",
            pick: "logo",
            reason: "the cleanest at the same fidelity",
            candidates: [
              { preset: "balanced", label: "Balanced", svg: "<svg>balanced</svg>", elapsed_ms: 800, stats: { paths: 9 } },
              { preset: "logo", label: "Logo & icon", svg: "<svg>logo</svg>", elapsed_ms: 900, stats: { paths: 4 } },
            ],
          },
        },
      }),
    );
    await flush();
    const item = lib.getState().items[0];
    expect(item.shown).toBe("auto");
    expect(item.job).toBeNull();
    expect(item.auto?.pick).toBe("logo");
    expect(item.params).toEqual({ detail: 10, min_region: 16 });
    expect(item.traces[paramsKey({ detail: 6, min_region: 8 })].svg).toBe("<svg>balanced</svg>");
    // a candidate is shown at once, nothing traced again
    lib.pickPreset("a", PRESETS[1]);
    expect(lib.getState().items[0].shown).toBe(paramsKey({ detail: 6, min_region: 8 }));
    expect(platform.vectorize).toHaveBeenCalledTimes(1);
  });

  it("re-traces by itself after a quick trace, and asks for Update after a slow one", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    calls[0].resolve(response("<svg>a</svg>", 500));
    await flush();
    lib.setParam("a", "detail", 7);
    vi.advanceTimersByTime(DEBOUNCE_MS - 1);
    expect(platform.vectorize).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(platform.vectorize).toHaveBeenCalledTimes(2);
    expect(calls[1].req.parameters).toEqual({ detail: 7, min_region: 8 });

    lib.pickPreset("b", PRESETS[1]);
    lib.generate("b");
    calls[2].resolve(response("<svg>b</svg>", 5000));
    await flush();
    lib.setParam("b", "detail", 9);
    vi.advanceTimersByTime(DEBOUNCE_MS * 4);
    expect(platform.vectorize).toHaveBeenCalledTimes(3);
    expect(needsUpdate(lib.getState().items[1])).toBe(true);
  });

  it("does not re-trace by itself when live updates are off", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, { ...DEFAULT_SETTINGS, liveUpdate: false });
    lib.add([ok("a")]);
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    calls[0].resolve(response("<svg>a</svg>", 100));
    await flush();
    lib.setParam("a", "detail", 7);
    vi.advanceTimersByTime(DEBOUNCE_MS * 4);
    expect(platform.vectorize).toHaveBeenCalledTimes(1);
  });

  it("a newer request supersedes the running one, whose late answer is dropped", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a")]);
    lib.pickPreset("a", PRESETS[1]);
    lib.generate("a");
    lib.setParam("a", "detail", 12);
    lib.generate("a");
    expect(calls[0].signal.aborted).toBe(true);
    calls[1].resolve(response("<svg>new</svg>", 100));
    calls[0].resolve(response("<svg>old</svg>", 100));
    await flush();
    const item = lib.getState().items[0];
    expect(item.traces[traceKey(item)].svg).toBe("<svg>new</svg>");
    expect(Object.values(item.traces).map((t) => t.svg)).not.toContain("<svg>old</svg>");
  });

  it("cancel drops the job at once and is not an error", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a")]);
    lib.generate("a");
    lib.cancel("a");
    expect(lib.getState().items[0].job).toBeNull();
    expect(calls[0].signal.aborted).toBe(true);
    calls[0].reject(new ApiError("cancelled", "Cancelled", 499));
    await flush();
    expect(lib.getState().items[0].error).toBeNull();
  });

  it("an engine's error and a failed request become the item's error", async () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    lib.generate("a");
    calls[0].resolve({ ...response("", 0), results: { vexel: { error: { code: "engine_crashed", message: "The trace crashed" } } } });
    lib.generate("b");
    calls[1].reject(new ApiError("engine_crashed", "The trace crashed", 500));
    await flush();
    expect(lib.getState().items.map((i) => i.error?.code)).toEqual(["engine_crashed", "engine_crashed"]);
    expect(lib.getState().items.map((i) => i.job)).toEqual([null, null]);
  });

  it("remove closes the image, cancels its job and moves the selection", () => {
    const { platform, calls } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b"), ok("c")]);
    lib.select("b");
    lib.generate("b");
    lib.remove("b");
    expect(platform.closeImage).toHaveBeenCalledWith("b");
    expect(calls[0].signal.aborted).toBe(true);
    expect(lib.getState().selected).toBe("c");
    lib.remove("c");
    expect(lib.getState().selected).toBe("a");
    lib.clear();
    expect(lib.getState()).toEqual({ items: [], failed: [], selected: null });
  });

  it("selectNext walks the list and stops at the ends", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([ok("a"), ok("b")]);
    lib.selectNext(1);
    expect(lib.getState().selected).toBe("b");
    lib.selectNext(-1);
    lib.selectNext(-1);
    expect(lib.getState().selected).toBe("a");
  });

  it("downscale reopens a too-large file at 2048 px in its place", async () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    lib.add([{ failed: { name: "huge.png", path: "/pics/huge.png", error: new ApiError("too_many_pixels", "too big", 400) } }]);
    await lib.downscale(0);
    expect(platform.openPaths).toHaveBeenCalledWith(["/pics/huge.png"], { downscale: true });
    expect(lib.getState().failed).toEqual([]);
    expect(lib.getState().items.map((i) => i.image.id)).toEqual(["small"]);
  });

  it("traces new images straight away when the setting says so", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, { ...DEFAULT_SETTINGS, traceOnOpen: true });
    lib.add([ok("a"), ok("b")]);
    expect(platform.vectorize).toHaveBeenCalledTimes(2);
  });

  it("notifies subscribers and hands out a new state object on every change", () => {
    const { platform } = fakePlatform();
    const lib = createLibrary(platform, CATALOG, DEFAULT_SETTINGS);
    const seen: unknown[] = [];
    const off = lib.subscribe(() => seen.push(lib.getState()));
    lib.add([ok("a")]);
    lib.select("a");
    off();
    lib.add([ok("b")]);
    expect(seen).toHaveLength(2);
    expect(seen[0]).not.toBe(seen[1]);
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npx vitest run src/state/library.test.ts`
Expected: FAIL, `Cannot find module './library'`.

- [ ] **Step 3: Write the store**

`frontend/src/state/library.ts`:

```ts
/**
 * The images open in the window, each with its own settings, every trace seen for it and the
 * job running for it. Framework-free: React reads it through `useLibrary`, tests call it directly.
 */
import { ApiError, type AutoResult, type EngineDescription, type ParamValues, type Preset, type VectorizeResponse } from "@/lib/api";
import { normalizeValues, paramsKey, specsFor } from "@/lib/schema";
import type { OpenedImage, OpenFailure, OpenOutcome, Phase, Platform, Settings } from "@/platform/types";

export const ENGINE = "vexel";
/** A trace quicker than this is run again by itself when its settings move. */
export const LIVE_MS = 2000;
/** How long a control must rest before a live update traces. */
export const DEBOUNCE_MS = 250;

export interface TraceAnswer {
  svg: string;
  elapsedMs: number;
  stats: Record<string, number>;
}

export interface Job {
  id: string;
  /** The settings key it traces. */
  key: string;
  startedAt: number;
  phase: Phase;
}

export interface ImageItem {
  image: OpenedImage;
  /** "auto", a preset's id, or null once a control has been moved by hand. */
  preset: string | null;
  /** The engine's values the panel shows; after an Auto run, those of Auto's pick. */
  params: ParamValues;
  /** Every answer seen for this image, by settings key ("auto" or `paramsKey(params)`). */
  traces: Record<string, TraceAnswer>;
  /** The key of the answer on screen; null before the first trace. */
  shown: string | null;
  job: Job | null;
  error: ApiError | null;
  /** The last Auto run on this image. */
  auto: AutoResult | null;
}

export interface LibraryState {
  items: ImageItem[];
  /** Files that could not be opened, kept in the sidebar with what went wrong. */
  failed: OpenFailure[];
  selected: string | null;
}

export interface Catalog {
  engine: EngineDescription;
  presets: Preset[];
}

export type LibraryPlatform = Pick<Platform, "vectorize" | "closeImage" | "openPaths">;

export interface Library {
  getState(): LibraryState;
  subscribe(listener: () => void): () => void;
  selectedItem(): ImageItem | null;
  add(outcomes: OpenOutcome[]): void;
  select(id: string | null): void;
  selectNext(delta: 1 | -1): void;
  remove(id: string): void;
  clear(): void;
  dismissFailure(index: number): void;
  /** Open a too-large file again, scaled to fit the cap, in place of its failure. */
  downscale(index: number): Promise<void>;
  pickPreset(id: string, preset: Preset): void;
  setParam(id: string, name: string, value: unknown): void;
  generate(id: string): void;
  cancel(id: string): void;
  setSettings(settings: Settings): void;
}

export function traceKey(item: Pick<ImageItem, "preset" | "params">): string {
  return item.preset === "auto" ? "auto" : paramsKey(item.params);
}

export function shownAnswer(item: ImageItem): TraceAnswer | null {
  return item.shown ? (item.traces[item.shown] ?? null) : null;
}

/** The settings have moved since the trace on screen, and no job is tracing them. */
export function needsUpdate(item: ImageItem): boolean {
  const key = traceKey(item);
  return item.shown !== null && item.shown !== key && item.job?.key !== key && !item.traces[key];
}

function isCancel(err: unknown): boolean {
  return (err instanceof ApiError && err.code === "cancelled") || (err as Error | null)?.name === "AbortError";
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError("engine_crashed", (err as Error | null)?.message || "The trace failed", 0, err);
}

let jobCounter = 0;
const newJobId = () => `job-${Date.now().toString(36)}-${(jobCounter += 1)}`;

export function createLibrary(platform: LibraryPlatform, catalog: Catalog, initialSettings: Settings): Library {
  const specs = specsFor(catalog.engine);
  const resolve = (params: ParamValues) => normalizeValues(specs, { ...catalog.engine.defaults, ...params });
  const defaults = resolve({});
  const presetById = new Map(catalog.presets.map((p) => [p.id, p]));

  let state: LibraryState = { items: [], failed: [], selected: null };
  let settings = initialSettings;
  const listeners = new Set<() => void>();
  const controllers = new Map<string, AbortController>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();

  const emit = () => listeners.forEach((l) => l());
  const set = (next: LibraryState) => {
    state = next;
    emit();
  };
  const find = (id: string) => state.items.find((i) => i.image.id === id);
  const patch = (id: string, change: Partial<ImageItem> | ((item: ImageItem) => Partial<ImageItem>)) =>
    set({ ...state, items: state.items.map((i) => (i.image.id === id ? { ...i, ...(typeof change === "function" ? change(i) : change) } : i)) });

  const abortJob = (item: ImageItem | undefined) => {
    if (!item?.job) return;
    controllers.get(item.job.id)?.abort();
    controllers.delete(item.job.id);
  };

  const answerOf = (res: VectorizeResponse): TraceAnswer | ApiError => {
    const r = res.results[ENGINE];
    if (!r || r.error || !r.svg) return new ApiError(r?.error?.code ?? "engine_failed", r?.error?.message ?? "The engine returned no vector");
    return { svg: r.svg, elapsedMs: r.elapsed_ms ?? 0, stats: r.stats ?? {} };
  };

  const finish = (id: string, jobId: string, key: string, res: VectorizeResponse) => {
    const item = find(id);
    if (!item || item.job?.id !== jobId) return; // superseded or cancelled
    const answer = answerOf(res);
    if (answer instanceof ApiError) {
      patch(id, { job: null, error: answer });
      return;
    }
    const traces = { ...item.traces, [key]: answer };
    const auto = res.auto?.[ENGINE] ?? null;
    let params = item.params;
    if (auto) {
      for (const c of auto.candidates) {
        const preset = presetById.get(c.preset);
        if (preset && c.svg) traces[paramsKey(resolve(preset.params))] = { svg: c.svg, elapsedMs: c.elapsed_ms ?? 0, stats: c.stats ?? {} };
      }
      const pick = auto.pick ? presetById.get(auto.pick) : undefined;
      if (pick) params = resolve(pick.params);
    }
    patch(id, { job: null, error: null, traces, shown: key, auto: auto ?? item.auto, params });
  };

  const fail = (id: string, jobId: string, err: unknown) => {
    const item = find(id);
    if (!item || item.job?.id !== jobId) return;
    patch(id, isCancel(err) ? { job: null } : { job: null, error: toApiError(err) });
  };

  const generate = (id: string) => {
    const item = find(id);
    if (!item) return;
    const key = traceKey(item);
    if (item.traces[key]) {
      patch(id, { shown: key, error: null });
      return;
    }
    if (item.job?.key === key) return;
    abortJob(item);
    const jobId = newJobId();
    const controller = new AbortController();
    controllers.set(jobId, controller);
    patch(id, { job: { id: jobId, key, startedAt: Date.now(), phase: "queued" }, error: null });
    const auto = key === "auto";
    platform
      .vectorize(
        { imageId: id, parameters: auto ? {} : item.params, auto, job: jobId },
        {
          signal: controller.signal,
          onPhase: (phase) => {
            const now = find(id);
            if (now?.job?.id === jobId) patch(id, { job: { ...now.job, phase } });
          },
        },
      )
      .then((res) => finish(id, jobId, key, res))
      .catch((err) => fail(id, jobId, err))
      .finally(() => controllers.delete(jobId));
  };

  /** After the settings of a traced image move: show what is known, or trace if traces here are quick. */
  const settle = (id: string, debounce: boolean) => {
    const item = find(id);
    if (!item) return;
    const key = traceKey(item);
    if (item.traces[key]) {
      patch(id, { shown: key });
      return;
    }
    const last = item.shown ? item.traces[item.shown] : undefined;
    if (!settings.liveUpdate || !last || last.elapsedMs >= LIVE_MS) return;
    clearTimeout(timers.get(id));
    if (!debounce) {
      generate(id);
      return;
    }
    timers.set(
      id,
      setTimeout(() => {
        timers.delete(id);
        generate(id);
      }, DEBOUNCE_MS),
    );
  };

  const selectAfterRemoving = (id: string): string | null => {
    const at = state.items.findIndex((i) => i.image.id === id);
    if (state.selected !== id) return state.selected;
    const rest = state.items.filter((i) => i.image.id !== id);
    return rest[Math.min(at, rest.length - 1)]?.image.id ?? null;
  };

  const lib: Library = {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    selectedItem: () => (state.selected ? (find(state.selected) ?? null) : null),

    add(outcomes) {
      const items = [...state.items];
      const failed = [...state.failed];
      const added: string[] = [];
      let selected = state.selected;
      for (const o of outcomes) {
        if ("failed" in o) {
          if (!failed.some((f) => f.name === o.failed.name && f.path === o.failed.path)) failed.push(o.failed);
          continue;
        }
        selected = o.ok.id;
        if (items.some((i) => i.image.id === o.ok.id)) continue;
        items.push({ image: o.ok, preset: "auto", params: defaults, traces: {}, shown: null, job: null, error: null, auto: null });
        added.push(o.ok.id);
      }
      set({ items, failed, selected });
      if (settings.traceOnOpen) added.forEach(generate);
    },

    select(id) {
      if (id === null || find(id)) set({ ...state, selected: id });
    },

    selectNext(delta) {
      const at = state.items.findIndex((i) => i.image.id === state.selected);
      const next = state.items[Math.max(0, Math.min(state.items.length - 1, at + delta))];
      if (next) set({ ...state, selected: next.image.id });
    },

    remove(id) {
      const item = find(id);
      if (!item) return;
      abortJob(item);
      clearTimeout(timers.get(id));
      platform.closeImage(id);
      set({ ...state, selected: selectAfterRemoving(id), items: state.items.filter((i) => i.image.id !== id) });
    },

    clear() {
      for (const item of state.items) {
        abortJob(item);
        clearTimeout(timers.get(item.image.id));
        platform.closeImage(item.image.id);
      }
      set({ items: [], failed: [], selected: null });
    },

    dismissFailure(index) {
      set({ ...state, failed: state.failed.filter((_, i) => i !== index) });
    },

    async downscale(index) {
      const failure = state.failed[index];
      if (!failure?.path) return;
      const outcomes = await platform.openPaths([failure.path], { downscale: true });
      set({ ...state, failed: state.failed.filter((f) => f !== failure) });
      lib.add(outcomes);
    },

    pickPreset(id, preset) {
      const item = find(id);
      if (!item) return;
      if (preset.kind === "auto") patch(id, { preset: "auto" });
      else patch(id, { preset: preset.id, params: resolve(preset.params) });
      settle(id, false);
    },

    setParam(id, name, value) {
      const item = find(id);
      if (!item) return;
      patch(id, { preset: null, params: normalizeValues(specs, { ...item.params, [name]: value }) });
      settle(id, true);
    },

    generate,

    cancel(id) {
      const item = find(id);
      if (!item?.job) return;
      abortJob(item);
      patch(id, { job: null });
    },

    setSettings(next) {
      settings = next;
    },
  };
  return lib;
}
```

`frontend/src/state/useLibrary.ts`:

```ts
import { useSyncExternalStore } from "react";

import type { ImageItem, Library, LibraryState } from "./library";

export function useLibrary(lib: Library): LibraryState {
  return useSyncExternalStore(lib.subscribe, lib.getState, lib.getState);
}

export function useSelected(lib: Library): ImageItem | null {
  const state = useLibrary(lib);
  return state.selected ? (state.items.find((i) => i.image.id === state.selected) ?? null) : null;
}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd frontend && npx vitest run src/state/library.test.ts`
Expected: PASS, 13 tests. Then `npm run test:run` (everything else still passes) and `npm run typecheck`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/state
git commit -m "frontend: the library store: images, their settings, traces and jobs, and the live-update rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The window: Mac styles, title bar, three panes, sidebar, empty state

The page becomes the concept's window: a unified title bar, the sidebar of images, the viewer and the Vectorize panel, on the Mac's type, density and accent. The viewer is still the old `Canvas` and the right panel the old `Presets` and `ParamPanel` with a Generate button (Tasks 9 and 10 replace both); the web-page parts (header, landing hero, dropzone, health pill, theme toggle, engine tabs) and the hooks the library replaced are removed.

**Files:**
- Modify: `frontend/tailwind.config.ts`, `frontend/src/styles.css`, `frontend/src/App.tsx` (rewrite), `frontend/src/App.test.tsx` (rewrite), `frontend/src/main.tsx`, `frontend/src/test/server.ts`, `frontend/index.html`
- Create: `frontend/src/components/TitleBar.tsx`, `AppShell.tsx`, `Sidebar.tsx`, `ImageCard.tsx`, `EmptyState.tsx`, `frontend/src/hooks/useLayerInspector.ts`, `frontend/src/hooks/useWindowDrop.ts`
- Test: `frontend/src/components/TitleBar.test.tsx`, `Sidebar.test.tsx`, `EmptyState.test.tsx`
- Delete (with `git rm`): `src/components/Header.tsx`, `StatusPill.tsx`, `ThemeToggle.tsx`, `Dropzone.tsx`, `Dropzone.test.tsx`, `Samples.tsx`, `EngineTabs.tsx`, `src/hooks/useUpload.ts`, `useUpload.test.tsx`, `useVectorize.ts`, `useVectorize.test.tsx`, `useParams.ts`, `useHealth.ts`, `useHealth.test.tsx`

**Interfaces:**
- Consumes: `platform`, `DEFAULT_SETTINGS`, `Settings`, `OpenOutcome`, `OpenFailure` (Task 6); `createLibrary`, `useLibrary`, `ENGINE`, `ImageItem`, `shownAnswer` (Task 7); the existing `Canvas`, `Presets`, `ParamPanel`, `Inspector`, `InspectorOverlay`, `tinyShapes`, `EMPTY_INSPECTOR`, `parseSvg`, `applyTheme`, `watchSystemTheme`, `specsFor`.
- Produces (used by Tasks 9–12):
  - CSS: `--window`, `--accent-mac`; classes `.app-shell`, `.panel`, `.panel-sidebar`, `.mac-primary`, `.mac-button`, `.mac-ghost`, `.mac-icon`, `.mac-selected`, `.mac-choice`, `.mac-radio`, `.mac-dot`, `.mac-tint`; Tailwind `font-brand`, `bg-window`; `html.native` set by `main.tsx` in the Mac app.
  - `TitleBar({ native, sidebar, inspector, onToggleSidebar, onToggleInspector, onSettings })`
  - `AppShell({ titleBar, sidebar, main, inspector, overlay })`
  - `Sidebar({ items, failed, selected, formats, canDownscale, onAdd, onSelect, onSelectNext, onClear, onDownscale, onDismissFailure, wrapCard? })`, `ImageCard({ item, selected, onSelect })`
  - `EmptyState({ onOpen, onSample })`, `SAMPLES`
  - `useLayerInspector(svg: string | undefined) => { doc, state, patch, dropped, exportSvg, liveState }`
  - `useWindowDrop(enabled, onFiles, onOver)`
  - In `App.tsx`: `Workspace` holds `lib`, `settings`, `sidebar`/`inspector` visibility and `open(promise)`; later tasks add to it.

- [ ] **Step 1: The tokens, the type and the utilities**

`frontend/tailwind.config.ts`: replace `fontFamily` and add `fontSize` and the `window` colour:

```ts
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", '"SF Pro Text"', '"Helvetica Neue"', "sans-serif"],
        brand: ["Poppins", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      // AppKit's sizes: 13 px body, 11 px secondary
      fontSize: {
        xs: ["11px", "14px"],
        sm: ["13px", "18px"],
        base: ["13px", "18px"],
        lg: ["15px", "20px"],
        xl: ["17px", "22px"],
        "2xl": ["22px", "28px"],
      },
```

and in `colors`: `window: hsl("window"),`.

`frontend/src/styles.css`:
- in `:root` add `--window: 220 14% 93%;` and `--accent-mac: #007aff;`; in `.dark` add `--window: 216 22% 9%;`
- after the `.dark` block add:

```css
/* The system's accent colour where WebKit exposes it (`AccentColor` is fixed blue in WebKit; this one follows
   System Settings ▸ Appearance), macOS blue elsewhere. */
@supports (color: -apple-system-control-accent) {
  :root {
    --accent-mac: -apple-system-control-accent;
  }
}
```

- replace the `body` rule and the `:focus-visible` rule in `@layer base` with:

```css
  body {
    @apply bg-window text-foreground font-sans text-sm antialiased;
    margin: 0;
    /* a window, not a page: nothing selects, nothing scrolls past its end, the cursor is the arrow */
    -webkit-user-select: none;
    user-select: none;
    cursor: default;
    overscroll-behavior: none;
  }
  html.native,
  html.native body {
    /* the window's vibrancy shows through where no panel is painted */
    background: transparent;
  }
  input,
  textarea,
  [contenteditable="true"],
  .selectable {
    -webkit-user-select: text;
    user-select: text;
    cursor: auto;
  }
  button,
  [role="option"],
  [role="radio"] {
    cursor: default;
  }
  :focus-visible {
    outline: 3px solid var(--accent-mac);
    outline-offset: 1px;
  }
  @supports (color: color-mix(in srgb, red 50%, blue)) {
    :focus-visible {
      outline-color: color-mix(in srgb, var(--accent-mac) 50%, transparent);
    }
  }
```

- add to `@layer components`:

```css
  .app-shell {
    @apply flex h-dvh flex-col overflow-hidden bg-window;
  }
  html.native .app-shell {
    background: transparent;
  }
  .panel {
    @apply rounded-xl border bg-card text-card-foreground;
  }
  .panel-sidebar {
    @apply rounded-xl border bg-card/60 text-card-foreground backdrop-blur-xl dark:bg-card/45;
  }
  .mac-primary {
    @apply inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg text-[13px] font-semibold text-white transition-[filter]
      disabled:opacity-50;
    background: var(--accent-mac);
  }
  .mac-primary:hover:not(:disabled) {
    filter: brightness(1.08);
  }
  .mac-primary:active:not(:disabled) {
    filter: brightness(0.94);
  }
  .mac-button {
    @apply inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border bg-background/70 px-3 text-[13px] font-medium
      transition-colors hover:bg-accent disabled:opacity-50;
  }
  .mac-ghost {
    @apply inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[13px] text-muted-foreground transition-colors
      hover:bg-accent hover:text-foreground disabled:opacity-40;
  }
  .mac-icon {
    @apply inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors
      hover:bg-accent hover:text-foreground disabled:opacity-40 aria-pressed:bg-accent aria-pressed:text-foreground;
  }
  .mac-selected {
    box-shadow: 0 0 0 2px var(--accent-mac);
  }
  .mac-dot {
    @apply inline-block h-1.5 w-1.5 rounded-full;
    background: var(--accent-mac);
  }
  .mac-tint {
    background: rgb(0 122 255 / 0.1);
  }
  .mac-choice {
    @apply rounded-lg p-2.5 transition-colors hover:bg-accent/60;
  }
  .mac-choice[aria-checked="true"] {
    background: rgb(0 122 255 / 0.1);
    box-shadow: inset 0 0 0 1px rgb(0 122 255 / 0.6);
  }
  .mac-radio {
    @apply inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 border-muted-foreground/40;
  }
  [aria-checked="true"] .mac-radio {
    border-color: var(--accent-mac);
    background: var(--accent-mac);
    box-shadow: inset 0 0 0 3px hsl(var(--card));
  }
  @supports (color: color-mix(in srgb, red 50%, blue)) {
    .mac-tint,
    .mac-choice[aria-checked="true"] {
      background: color-mix(in srgb, var(--accent-mac) 10%, transparent);
    }
    .mac-choice[aria-checked="true"] {
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-mac) 60%, transparent);
    }
  }
```

In `frontend/src/main.tsx`, after the imports: `if (platform.kind === "native") document.documentElement.classList.add("native");` (import `platform` from `./platform`), drop `applyTheme(getTheme())` (App applies the setting), and give the Toaster `position="bottom-center"`. In `frontend/index.html`, set `<title>Studi0Trace</title>`.

- [ ] **Step 2: Write the failing component tests**

`frontend/src/components/TitleBar.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TitleBar } from "./TitleBar";

describe("TitleBar", () => {
  it("names the app, is a drag region and toggles the panes", () => {
    const onToggleSidebar = vi.fn();
    const onSettings = vi.fn();
    const { container } = render(<TitleBar native sidebar inspector={false} onToggleSidebar={onToggleSidebar} onToggleInspector={vi.fn()} onSettings={onSettings} />);
    expect(screen.getByRole("heading", { name: "Studi0Trace" })).toBeInTheDocument();
    expect(screen.getByText("Turn images into clean vectors")).toBeInTheDocument();
    const bar = container.querySelector("header")!;
    expect(bar.getAttribute("data-tauri-drag-region")).toBe("deep");
    expect(bar.style.paddingLeft).toBe("88px");
    expect(screen.getByRole("button", { name: "Show sidebar" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Show inspector" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(onToggleSidebar).toHaveBeenCalledOnce();
    expect(onSettings).toHaveBeenCalledOnce();
  });

  it("leaves no room for traffic lights in a browser", () => {
    const { container } = render(<TitleBar native={false} sidebar inspector onToggleSidebar={vi.fn()} onToggleInspector={vi.fn()} onSettings={vi.fn()} />);
    expect(container.querySelector("header")!.style.paddingLeft).toBe("16px");
  });
});
```

`frontend/src/components/Sidebar.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { ImageItem } from "@/state/library";
import { Sidebar, type SidebarProps } from "./Sidebar";

const item = (id: string, patch: Partial<ImageItem> = {}): ImageItem => ({
  image: { id, name: `${id}.png`, path: `/p/${id}.png`, width: 1200, height: 800, format: "PNG", previewUrl: `blob:${id}` },
  preset: "auto",
  params: {},
  traces: {},
  shown: null,
  job: null,
  error: null,
  auto: null,
  ...patch,
});

function setup(patch: Partial<SidebarProps> = {}) {
  const props: SidebarProps = {
    items: [item("a"), item("b", { job: { id: "j", key: "auto", startedAt: 0, phase: "queued" } }), item("c", { error: new ApiError("engine_crashed", "The trace crashed (signal 9)") })],
    failed: [],
    selected: "a",
    formats: "PNG, JPG, HEIC, etc.",
    canDownscale: true,
    onAdd: vi.fn(),
    onSelect: vi.fn(),
    onSelectNext: vi.fn(),
    onClear: vi.fn(),
    onDownscale: vi.fn(),
    onDismissFailure: vi.fn(),
    ...patch,
  };
  render(<Sidebar {...props} />);
  return props;
}

describe("Sidebar", () => {
  it("lists every image with its size and status, the selected one marked", () => {
    setup();
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    expect(screen.getByText("a.png")).toBeInTheDocument();
    expect(screen.getAllByText(/1200 × 800/)).toHaveLength(3);
    expect(screen.getByText(/Queued/)).toBeInTheDocument();
    expect(screen.getByTitle("The trace crashed (signal 9)")).toBeInTheDocument();
  });

  it("adds, selects, walks with the arrow keys and clears", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /Add Image/ }));
    fireEvent.click(screen.getByText("b.png"));
    fireEvent.keyDown(screen.getByRole("listbox", { name: "Images" }), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("listbox", { name: "Images" }), { key: "ArrowUp" });
    fireEvent.click(screen.getByRole("button", { name: /Clear All/ }));
    expect(props.onAdd).toHaveBeenCalledOnce();
    expect(props.onSelect).toHaveBeenCalledWith("b");
    expect(props.onSelectNext.mock.calls).toEqual([[1], [-1]]);
    expect(props.onClear).toHaveBeenCalledOnce();
  });

  it("offers Downscale for a file over the cap, only where a path and the Mac app allow it", () => {
    const failed = [
      { name: "huge.png", path: "/p/huge.png", error: new ApiError("too_many_pixels", "Image exceeds the 2048x2048 pixel limit") },
      { name: "junk.bin", path: "/p/junk.bin", error: new ApiError("unsupported_format", "File is not a recognised image") },
    ];
    const props = setup({ items: [], failed, selected: null });
    expect(screen.getAllByRole("button", { name: "Downscale to 2048 px" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Downscale to 2048 px" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    expect(props.onDownscale).toHaveBeenCalledWith(0);
    expect(props.onDismissFailure).toHaveBeenCalledWith(1);
  });

  it("does not offer Downscale in a browser, and Clear All is off with nothing to clear", () => {
    setup({ items: [], failed: [{ name: "huge.png", path: null, error: new ApiError("too_many_pixels", "too big") }], canDownscale: false, selected: null });
    expect(screen.queryByRole("button", { name: "Downscale to 2048 px" })).toBeNull();
    setup({ items: [], failed: [], selected: null });
    expect(screen.getAllByRole("button", { name: /Clear All/ }).at(-1)).toBeDisabled();
  });
});
```

`frontend/src/components/EmptyState.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmptyState, SAMPLES } from "./EmptyState";

afterEach(() => vi.restoreAllMocks());

describe("EmptyState", () => {
  it("asks for images and opens the panel", () => {
    const onOpen = vi.fn();
    render(<EmptyState onOpen={onOpen} onSample={vi.fn()} />);
    expect(screen.getByText("Drop images here")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open…" }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("opens a sample as a file", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new Blob([new Uint8Array([1, 2])], { type: "image/png" })));
    const onSample = vi.fn();
    render(<EmptyState onOpen={vi.fn()} onSample={onSample} />);
    fireEvent.click(screen.getByRole("button", { name: SAMPLES[0].label }));
    await waitFor(() => expect(onSample).toHaveBeenCalledOnce());
    expect((onSample.mock.calls[0][0] as File).name).toBe(SAMPLES[0].name);
  });
});
```

Run: `cd frontend && npx vitest run src/components/TitleBar.test.tsx src/components/Sidebar.test.tsx src/components/EmptyState.test.tsx`
Expected: FAIL (the components do not exist).

- [ ] **Step 3: Write the components**

`frontend/src/components/TitleBar.tsx`:

```tsx
import { PanelLeft, PanelRight, Settings as Gear } from "lucide-react";

export interface TitleBarProps {
  /** In the Mac app the traffic lights sit at the left of the bar. */
  native: boolean;
  sidebar: boolean;
  inspector: boolean;
  onToggleSidebar: () => void;
  onToggleInspector: () => void;
  onSettings: () => void;
}

/** The unified title bar: the window drags by all of it but its buttons, and a double-click zooms it. */
export function TitleBar({ native, sidebar, inspector, onToggleSidebar, onToggleInspector, onSettings }: TitleBarProps) {
  return (
    <header data-tauri-drag-region="deep" className="flex h-[52px] shrink-0 items-center gap-2.5 pr-3" style={{ paddingLeft: native ? 88 : 16 }}>
      <img src="/brand/studi0trace-mark.svg" alt="" aria-hidden="true" draggable={false} className="h-7 w-7" />
      <div className="min-w-0 leading-tight">
        <h1 className="font-brand text-[15px] font-semibold tracking-tight">Studi0Trace</h1>
        <p className="text-[11px] text-muted-foreground">Turn images into clean vectors</p>
      </div>
      <div className="ml-auto flex items-center gap-0.5">
        <button type="button" className="mac-icon" aria-label="Show sidebar" aria-pressed={sidebar} title="Show Sidebar (⌃⌘S)" onClick={onToggleSidebar}>
          <PanelLeft className="h-4 w-4" aria-hidden="true" />
        </button>
        <button type="button" className="mac-icon" aria-label="Show inspector" aria-pressed={inspector} title="Show Inspector (⌥⌘I)" onClick={onToggleInspector}>
          <PanelRight className="h-4 w-4" aria-hidden="true" />
        </button>
        <button type="button" className="mac-icon" aria-label="Settings" title="Settings (⌘,)" onClick={onSettings}>
          <Gear className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
```

`frontend/src/components/AppShell.tsx`:

```tsx
import type { ReactNode } from "react";

export interface AppShellProps {
  titleBar: ReactNode;
  sidebar: ReactNode | null;
  main: ReactNode;
  inspector: ReactNode | null;
  /** Painted over everything, e.g. the drop target while files are dragged in. */
  overlay?: ReactNode;
}

/** Three rounded panels on the window: 8 px from its edges and from each other. */
export function AppShell({ titleBar, sidebar, main, inspector, overlay }: AppShellProps) {
  return (
    <div className="app-shell">
      {titleBar}
      <div className="flex min-h-0 flex-1 gap-2 px-2 pb-2">
        {sidebar && (
          <aside aria-label="Images" className="panel-sidebar flex w-[232px] shrink-0 flex-col overflow-hidden">
            {sidebar}
          </aside>
        )}
        <main className="panel relative flex min-w-0 flex-1 flex-col overflow-hidden">{main}</main>
        {inspector && (
          <aside aria-label="Vectorize" className="panel flex w-[300px] shrink-0 flex-col overflow-hidden">
            {inspector}
          </aside>
        )}
      </div>
      {overlay}
    </div>
  );
}
```

`frontend/src/components/ImageCard.tsx`:

```tsx
import type { ImageItem } from "@/state/library";

export interface ImageCardProps {
  item: ImageItem;
  selected: boolean;
  onSelect: () => void;
}

/** One image in the sidebar: its thumbnail, name and size, and a dot while it is queued, tracing or failed. */
export function ImageCard({ item, selected, onSelect }: ImageCardProps) {
  const { image, job, error } = item;
  const phase = job ? (job.phase === "queued" ? "Queued" : "Tracing…") : null;
  return (
    <div id={`image-${image.id}`} role="option" aria-selected={selected} onClick={onSelect} className="block">
      <div className={`checker relative aspect-[4/3] overflow-hidden rounded-lg ${selected ? "mac-selected" : "ring-1 ring-border"}`}>
        <img src={image.previewUrl} alt="" draggable={false} className="h-full w-full object-contain p-2" />
        {job && <span aria-hidden="true" className={`absolute right-2 top-2 h-1.5 w-1.5 rounded-full ${job.phase === "queued" ? "bg-warning" : "mac-dot animate-pulse"}`} />}
        {!job && error && <span title={error.message} className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-destructive" />}
      </div>
      <p className="mt-1.5 truncate text-[13px] font-medium" title={image.path ?? image.name}>
        {image.name}
      </p>
      <p className="tabular text-[11px] text-muted-foreground">
        {image.width} × {image.height}
        {phase && ` · ${phase}`}
      </p>
    </div>
  );
}
```

`frontend/src/components/Sidebar.tsx`:

```tsx
import { ImageOff, Plus, Trash2 } from "lucide-react";
import { Fragment, type KeyboardEvent, type ReactNode } from "react";

import type { OpenFailure } from "@/platform/types";
import type { ImageItem } from "@/state/library";
import { ImageCard } from "./ImageCard";

export interface SidebarProps {
  items: ImageItem[];
  failed: OpenFailure[];
  selected: string | null;
  /** Under "Add Image": what can be opened here. */
  formats: string;
  /** Whether "Downscale to 2048 px" can be offered (the Mac app, a file with a path). */
  canDownscale: boolean;
  onAdd: () => void;
  onSelect: (id: string) => void;
  onSelectNext: (delta: 1 | -1) => void;
  onClear: () => void;
  onDownscale: (index: number) => void;
  onDismissFailure: (index: number) => void;
  /** Wraps each card, e.g. in its context menu. */
  wrapCard?: (item: ImageItem, card: ReactNode) => ReactNode;
}

const TOO_BIG = new Set(["too_many_pixels", "too_large"]);

export function Sidebar({ items, failed, selected, formats, canDownscale, onAdd, onSelect, onSelectNext, onClear, onDownscale, onDismissFailure, wrapCard = (_, card) => card }: SidebarProps) {
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      onSelectNext(1);
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      onSelectNext(-1);
    }
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="p-2">
        <button type="button" onClick={onAdd} className="flex w-full items-center gap-3 rounded-lg bg-background/70 p-2.5 text-left transition-colors hover:bg-background">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-background shadow-sm">
            <Plus className="h-4 w-4" aria-hidden="true" />
          </span>
          <span className="min-w-0">
            <span className="block text-[13px] font-semibold">Add Image</span>
            <span className="block truncate text-[11px] text-muted-foreground">{formats}</span>
          </span>
        </button>
      </div>
      <div
        role="listbox"
        aria-label="Images"
        tabIndex={items.length ? 0 : -1}
        aria-activedescendant={selected ? `image-${selected}` : undefined}
        onKeyDown={onKeyDown}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pb-3"
      >
        {items.map((item) => (
          <Fragment key={item.image.id}>{wrapCard(item, <ImageCard item={item} selected={item.image.id === selected} onSelect={() => onSelect(item.image.id)} />)}</Fragment>
        ))}
        {failed.map((f, i) => (
          <div key={`${f.path ?? f.name}-${i}`} role="group" aria-label={`${f.name} could not be opened`} className="rounded-lg bg-background/60 p-2.5">
            <div className="flex items-start gap-2">
              <ImageOff className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium">{f.name}</p>
                <p className="text-[11px] leading-snug text-muted-foreground">{f.error.message}</p>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {canDownscale && f.path && TOO_BIG.has(f.error.code) && (
                <button type="button" className="mac-button h-7 px-2 text-[12px]" onClick={() => onDownscale(i)}>
                  Downscale to 2048 px
                </button>
              )}
              <button type="button" className="mac-button h-7 px-2 text-[12px]" onClick={() => onDismissFailure(i)}>
                Remove
              </button>
            </div>
          </div>
        ))}
      </div>
      <div className="border-t p-2">
        <button type="button" className="mac-ghost w-full" disabled={!items.length && !failed.length} onClick={onClear}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
          Clear All
        </button>
      </div>
    </div>
  );
}
```

`frontend/src/components/EmptyState.tsx`:

```tsx
import { ImagePlus } from "lucide-react";

export const SAMPLES = [
  { name: "logo.png", label: "Logo" },
  { name: "sticker.png", label: "Flat art" },
  { name: "gradient.png", label: "Gradient" },
  { name: "shadow.png", label: "Shadow" },
] as const;

export interface EmptyStateProps {
  onOpen: () => void;
  onSample: (file: File) => void;
}

/** The viewer with nothing open: a drop target, the open panel, and samples to try. */
export function EmptyState({ onOpen, onSample }: EmptyStateProps) {
  const pick = async (name: string) => {
    const res = await fetch(`/samples/${name}`);
    const blob = await res.blob();
    onSample(new File([blob], name, { type: blob.type || "image/png" }));
  };
  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 p-8 text-center">
      <div className="flex w-full max-w-md flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-border px-10 py-12">
        <ImagePlus className="h-9 w-9 text-muted-foreground" aria-hidden="true" />
        <p className="text-[15px] font-semibold">Drop images here</p>
        <p className="text-muted-foreground">PNG, JPEG, GIF, WebP, BMP, HEIC or TIFF, up to 2048 px a side</p>
        <button type="button" className="mac-button mt-2" onClick={onOpen}>
          Open…
        </button>
      </div>
      <div className="space-y-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Or try a sample</p>
        <div className="flex flex-wrap justify-center gap-2">
          {SAMPLES.map((s) => (
            <button key={s.name} type="button" className="mac-button gap-2 pl-1.5" onClick={() => void pick(s.name)}>
              <img src={`/samples/${s.name}`} alt="" className="checker h-6 w-6 rounded object-contain" />
              {s.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
```

`frontend/src/hooks/useLayerInspector.ts` (moved out of the old `App.tsx`, unchanged in behaviour):

```ts
import { useCallback, useEffect, useMemo, useState } from "react";

import { EMPTY_INSPECTOR, tinyShapes, type InspectorState } from "@/components/Inspector";
import { parseSvg } from "@/lib/svgdoc";

/** The layer inspector's state for one SVG: shapes hidden by hand or by size, and the SVG to export without them. */
export function useLayerInspector(svg: string | undefined) {
  const [state, setState] = useState<InspectorState>(EMPTY_INSPECTOR);
  const patch = useCallback((p: Partial<InspectorState>) => setState((prev) => ({ ...prev, ...p })), []);
  const doc = useMemo(() => (svg ? parseSvg(svg) : null), [svg]);
  // a new trace renumbers the shapes, so per-shape state cannot carry over
  useEffect(() => setState((prev) => ({ ...prev, hidden: new Set(), highlight: null, minArea: 0 })), [svg]);
  const dropped = useMemo(() => {
    if (!doc) return new Set<number>();
    const out = new Set(state.hidden);
    for (const i of tinyShapes(doc, state.minArea)) out.add(i);
    return out;
  }, [doc, state.hidden, state.minArea]);
  const exportSvg = useMemo(() => (doc && dropped.size ? doc.render(dropped) : svg), [doc, dropped, svg]);
  const liveState = useMemo(() => ({ ...state, hidden: dropped }), [state, dropped]);
  return { doc, state, patch, dropped, exportSvg, liveState };
}
```

`frontend/src/hooks/useWindowDrop.ts`:

```ts
import { useEffect } from "react";

/** In a browser, files dropped anywhere on the page are opened (the Mac app gets drops from Rust instead). */
export function useWindowDrop(enabled: boolean, onFiles: (files: File[]) => void, onOver: (over: boolean) => void) {
  useEffect(() => {
    if (!enabled) return;
    let depth = 0;
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth += 1;
      onOver(true);
    };
    const over = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) onOver(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      onOver(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) onFiles(files);
    };
    document.addEventListener("dragenter", enter);
    document.addEventListener("dragover", over);
    document.addEventListener("dragleave", leave);
    document.addEventListener("drop", drop);
    return () => {
      document.removeEventListener("dragenter", enter);
      document.removeEventListener("dragover", over);
      document.removeEventListener("dragleave", leave);
      document.removeEventListener("drop", drop);
    };
  }, [enabled, onFiles, onOver]);
}
```

Run: `cd frontend && npx vitest run src/components/TitleBar.test.tsx src/components/Sidebar.test.tsx src/components/EmptyState.test.tsx`
Expected: PASS (8 tests).

- [ ] **Step 4: Rewrite the app on the library**

`frontend/src/test/server.ts`: add a Vexel engine and its presets beside the old fixtures and serve those (the old `ENGINES`/`PRESETS` stay exported for the component tests that still use them until Tasks 9–10):

```ts
export const VEXEL: EngineDescription = {
  id: "vexel",
  label: "Vexel",
  description: "Faithful colour vectors.",
  primary: true,
  params: {
    properties: {
      detail: { type: "number", default: 6, minimum: 1, maximum: 20, description: "Colour detail", ui: { control: "slider", group: "Shapes", label: "Detail" } },
      min_region: { type: "integer", default: 8, minimum: 1, maximum: 64, ui: { control: "slider", group: "Shapes", label: "Smallest shape", unit: "px" } },
    },
  },
  defaults: { detail: 6, min_region: 8 },
};

export const VEXEL_PRESETS: Preset[] = [
  { id: "auto", label: "Auto", engine: "vexel", kind: "auto", description: "Traces your image with Balanced and Logo & icon, and keeps the cleanest result. Start here.", detail: "ΔE 0.22 · 3 shapes", sample: "auto.png", params: {} },
  { id: "balanced", label: "Balanced", engine: "vexel", kind: "preset", auto_candidate: true, description: "Gradients, shadows, strokes and overlaps all reconstructed. The most faithful all-rounder.", detail: "ΔE 0.25 · 3 shapes", sample: "balanced.png", params: {} },
  { id: "logo", label: "Logo & icon", engine: "vexel", kind: "preset", auto_candidate: true, description: "Merges harder and fits whole shapes, for a small, clean file.", detail: "ΔE 0.31 · 3 shapes", sample: "logo.png", params: { detail: 10, min_region: 16 } },
  { id: "flat", label: "Flat & poster", engine: "vexel", kind: "preset", description: "A style choice, not a quality setting: solid colours only.", detail: "ΔE 0.62 · 7 shapes", sample: "flat.png", params: { detail: 12 } },
];
```

and in `handlers` serve `[VEXEL]` for `/engines` and `VEXEL_PRESETS` for `/presets`; in the `/vectorize` handler and `autoResult`, look presets up in `VEXEL_PRESETS` and the engine in `[VEXEL, ...ENGINES]`, with Logo & icon as Auto's pick (`pick: "logo"`, `scores(0.31, 0, [], 3)` for logo and `scores(0.25, 3.2, ["2 pinholes"], 6)` for balanced).

`frontend/src/App.tsx` (complete; Tasks 9–12 change the marked parts):

```tsx
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { AppShell } from "./components/AppShell";
import { Canvas } from "./components/Canvas";
import { EmptyState } from "./components/EmptyState";
import { Inspector, InspectorOverlay } from "./components/Inspector";
import { ParamPanel } from "./components/ParamPanel";
import { Presets } from "./components/Presets";
import { Sidebar } from "./components/Sidebar";
import { TitleBar } from "./components/TitleBar";
import { useLayerInspector } from "./hooks/useLayerInspector";
import { useWindowDrop } from "./hooks/useWindowDrop";
import { specsFor } from "./lib/schema";
import { applyTheme, watchSystemTheme } from "./lib/theme";
import { DEFAULT_SETTINGS, platform, type OpenOutcome, type Settings } from "./platform";
import { createLibrary, ENGINE, shownAnswer, type Catalog } from "./state/library";
import { useLibrary } from "./state/useLibrary";

const FORMATS = platform.kind === "native" ? "PNG, JPG, HEIC, etc." : "PNG, JPG, GIF, WebP, BMP";

export default function App() {
  const engines = useQuery({ queryKey: ["engines"], queryFn: ({ signal }) => platform.engines(signal) });
  const presets = useQuery({ queryKey: ["presets"], queryFn: ({ signal }) => platform.presets(signal) });
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  useEffect(() => {
    void platform.loadSettings().then(setSettings);
    return platform.onSettings(setSettings);
  }, []);
  useEffect(() => {
    applyTheme(settings.appearance);
    return watchSystemTheme(() => settings.appearance);
  }, [settings.appearance]);

  const engine = engines.data?.find((e) => e.id === ENGINE);
  const catalog = useMemo<Catalog | null>(() => (engine && presets.data ? { engine, presets: presets.data.filter((p) => p.engine === ENGINE) } : null), [engine, presets.data]);
  const failure = engines.error ?? presets.error;
  if (failure || !catalog) {
    return (
      <AppShell
        titleBar={<TitleBar native={platform.kind === "native"} sidebar={false} inspector={false} onToggleSidebar={() => {}} onToggleInspector={() => {}} onSettings={() => {}} />}
        sidebar={null}
        inspector={null}
        main={
          <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center" role={failure ? "alert" : "status"}>
            <p className="text-[15px] font-semibold">{failure ? "Studi0Trace could not start" : "Starting…"}</p>
            {failure && <p className="max-w-sm text-muted-foreground">{failure.message}</p>}
            {failure && (
              <button type="button" className="mac-button" onClick={() => void Promise.all([engines.refetch(), presets.refetch()])}>
                Try Again
              </button>
            )}
          </div>
        }
      />
    );
  }
  return <Workspace catalog={catalog} settings={settings} />;
}

function Workspace({ catalog, settings }: { catalog: Catalog; settings: Settings }) {
  // one library for the window's life; the settings reach it as they change
  const lib = useMemo(() => createLibrary(platform, catalog, settings), [catalog]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => lib.setSettings(settings), [lib, settings]);
  const state = useLibrary(lib);
  const item = state.selected ? (state.items.find((i) => i.image.id === state.selected) ?? null) : null;
  const answer = item ? shownAnswer(item) : null;
  const [sidebar, setSidebar] = useState(true);
  const [inspectorPane, setInspectorPane] = useState(true);
  const [dragging, setDragging] = useState(false);
  const specs = useMemo(() => specsFor(catalog.engine), [catalog.engine]);

  const open = useCallback(
    async (outcomes: Promise<OpenOutcome[]>) => {
      try {
        lib.add(await outcomes);
      } catch (err) {
        toast.error((err as Error).message);
      }
    },
    [lib],
  );
  const openFiles = useCallback((files: File[]) => void open(platform.openFiles(files)), [open]);
  useEffect(() => platform.onOpenPaths((paths) => void open(platform.openPaths(paths))), [open]);
  useEffect(() => platform.onDragState(setDragging), []);
  useWindowDrop(platform.kind === "web", openFiles, setDragging);

  const layers = useLayerInspector(answer?.svg);

  // ── the viewer (Task 9 replaces this with <Viewer>)
  const viewer = item ? (
    <Canvas
      sourceUrl={item.image.previewUrl}
      svg={layers.exportSvg}
      width={item.image.width}
      height={item.image.height}
      updating={!!item.job}
      busyLabel={item.job?.phase === "queued" ? "Queued…" : item.job?.key === "auto" ? "Trying presets…" : "Tracing…"}
      errorMessage={item.error?.message}
      onRetry={() => lib.generate(item.image.id)}
      display={{ points: layers.state.points, outlines: layers.state.outlines }}
      onDisplayChange={layers.patch}
      marks={layers.doc ? (scale) => <InspectorOverlay doc={layers.doc!} state={layers.liveState} scale={scale} /> : undefined}
      panel={
        layers.doc && layers.state.open ? (
          <Inspector doc={layers.doc} bytes={layers.exportSvg?.length ?? 0} elapsedMs={answer?.elapsedMs} engineLabel={catalog.engine.label} edited={layers.dropped.size > 0} state={layers.liveState} onChange={layers.patch} />
        ) : undefined
      }
    />
  ) : (
    <EmptyState onOpen={() => void open(platform.pickImages())} onSample={(f) => openFiles([f])} />
  );

  // ── the right panel (Task 10 replaces this with <VectorizePanel>)
  const panel = item ? (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        <Presets
          presets={catalog.presets}
          defaults={catalog.engine.defaults}
          values={item.params}
          onPick={(p) => lib.pickPreset(item.image.id, p)}
          active={item.preset === "auto" ? "auto" : (item.preset ?? undefined)}
          auto={item.auto}
          autoRunning={item.job?.key === "auto"}
        />
        <ParamPanel engine={ENGINE} specs={specs} values={item.params} onChange={(name, value) => lib.setParam(item.image.id, name, value)} />
      </div>
      <div className="border-t p-3">
        <button type="button" className="mac-primary" onClick={() => lib.generate(item.image.id)} disabled={!!item.job}>
          Generate Vector
        </button>
      </div>
    </div>
  ) : (
    <p className="p-4 text-muted-foreground">Open an image to vectorize it.</p>
  );

  return (
    <AppShell
      titleBar={
        <TitleBar
          native={platform.kind === "native"}
          sidebar={sidebar}
          inspector={inspectorPane}
          onToggleSidebar={() => setSidebar((v) => !v)}
          onToggleInspector={() => setInspectorPane((v) => !v)}
          onSettings={() => platform.openSettingsWindow()}
        />
      }
      sidebar={
        sidebar ? (
          <Sidebar
            items={state.items}
            failed={state.failed}
            selected={state.selected}
            formats={FORMATS}
            canDownscale={platform.kind === "native"}
            onAdd={() => void open(platform.pickImages())}
            onSelect={lib.select}
            onSelectNext={lib.selectNext}
            onClear={lib.clear}
            onDownscale={(i) => void lib.downscale(i)}
            onDismissFailure={lib.dismissFailure}
          />
        ) : null
      }
      main={viewer}
      inspector={inspectorPane ? panel : null}
      overlay={
        dragging ? (
          <div className="pointer-events-none fixed inset-2 top-[60px] z-50 flex items-center justify-center rounded-xl border-2 border-dashed mac-tint" style={{ borderColor: "var(--accent-mac)" }}>
            <p className="text-[15px] font-semibold">Drop to add</p>
          </div>
        ) : null
      }
    />
  );
}
```

`frontend/src/App.test.tsx` (complete):

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import App from "./App";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

const png = (name = "logo.png") => new File([new Uint8Array([137, 80, 78, 71, 9, 9])], name, { type: "image/png" });

function drop(files: File[]) {
  const dataTransfer = { types: ["Files"], files };
  fireEvent.dragEnter(document, { dataTransfer });
  fireEvent.drop(document, { dataTransfer });
}

describe("App", () => {
  it("opens on an empty window with the title bar and the samples", async () => {
    renderApp();
    expect(await screen.findByText("Drop images here")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Studi0Trace" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Images" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Logo" })).toBeInTheDocument();
  });

  it("adds a dropped image to the sidebar, selected, on Auto and untraced", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    drop([png()]);
    const option = await screen.findByRole("option", { name: /logo\.png/ });
    expect(option).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Canvas" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Vector result" })).toBeNull();
    expect(screen.getByRole("button", { name: /Auto/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("Generate traces with Auto and shows the pick", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    drop([png()]);
    await screen.findByRole("option", { name: /logo\.png/ });
    fireEvent.click(screen.getByRole("button", { name: "Generate Vector" }));
    expect(await screen.findByRole("img", { name: "Vector result" })).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: "Vectorize" })).getByLabelText(/Auto chose Logo & icon/)).toBeInTheDocument();
  });

  it("hides and shows the panes from the title bar", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    expect(screen.queryByRole("complementary", { name: "Images" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show inspector" }));
    expect(screen.queryByRole("complementary", { name: "Vectorize" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    await waitFor(() => expect(screen.getByRole("complementary", { name: "Images" })).toBeInTheDocument());
  });

  it("says so when the server cannot be reached", async () => {
    const { http, HttpResponse } = await import("msw");
    const { API_URL } = await import("@/lib/api");
    server.use(http.get(`${API_URL}/engines`, () => HttpResponse.error()));
    renderApp();
    expect(await screen.findByText("Studi0Trace could not start")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try Again" })).toBeInTheDocument();
  });
});
```

Delete the replaced files:

```bash
cd frontend && git rm -q src/components/Header.tsx src/components/StatusPill.tsx src/components/ThemeToggle.tsx src/components/Dropzone.tsx src/components/Dropzone.test.tsx src/components/Samples.tsx src/components/EngineTabs.tsx src/hooks/useUpload.ts src/hooks/useUpload.test.tsx src/hooks/useVectorize.ts src/hooks/useVectorize.test.tsx src/hooks/useParams.ts src/hooks/useHealth.ts src/hooks/useHealth.test.tsx
```

(`lib/theme.ts`'s `getTheme`/`setTheme` may now be unused; leave them unless `npm run build` complains, then remove what is unused.)

- [ ] **Step 5: Run everything**

Run: `cd frontend && npm run test:run && npm run build`
Expected: every test passes; the build succeeds.

- [ ] **Step 6: Look at it**

Start `backend` and `frontend` from `.claude/launch.json` (preview_start each), open `http://localhost:5173`, then screenshot: the empty window; after clicking the "Logo" sample; after Generate Vector. In light and in dark (`resize_window` `colorScheme`), at 1280 × 800 and 960 × 640. Check against the concept: three rounded panels on the window colour, 8 px apart; the title bar with the mark, the wordmark in Poppins and the subtitle; the sidebar card with a ring round the selected thumbnail; 13 px system type; nothing selectable by drag; no page scroll. Then `cd apps/desktop && npm run dev` (if the controller can see the Mac): the traffic lights sit in the title bar to the left of the mark, the sidebar shows the vibrancy, the window drags by its title bar.

- [ ] **Step 7: Commit**

```bash
git add -A frontend
git commit -m "frontend: the Mac window: title bar, sidebar of images, empty state, the library behind it; the web page goes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The viewer

`Canvas` becomes `Viewer`: the compare modes, pan and zoom as before, with the concept's chrome (chips over the two sides, a round split handle, a floating toolbar at the foot), Mac gestures (two-finger scroll pans, pinch zooms, space-drag pans, a zoom tool), the checkerboard only under the image, and a handle the menu drives (zoom in/out, actual size, fit). The mode is the workspace's, so the View menu and ⌘1–⌘4 can set it.

**Files:**
- Rename: `frontend/src/components/Canvas.tsx` → `Viewer.tsx`, `Canvas.test.tsx` → `Viewer.test.tsx` (`git mv`, then rewrite)
- Create: `frontend/src/components/ViewerToolbar.tsx`
- Modify: `frontend/src/App.tsx` (use `Viewer`), `frontend/package.json` (`@radix-ui/react-dropdown-menu`)

**Interfaces:**
- Consumes: `ViewMode` (Task 6), `formatPercent`, the layer inspector's `marks`/`panel` (Task 8's `useLayerInspector`).
- Produces:
  - `Viewer` (`forwardRef<ViewerHandle, ViewerProps>`): `ViewerProps { sourceUrl, svg, width, height, mode, onModeChange(mode), busy: string | null, errorMessage?, onRetry?, display: {points, outlines}, onDisplayChange(patch), layersOpen, onToggleLayers(), marks?, panel? }`; `ViewerHandle { zoomIn(), zoomOut(), actualSize(), fit() }`.
  - `ViewerToolbar` and `type Tool = "pan" | "zoom"`; `ZOOM_LEVELS = [0.5, 1, 2, 4]`.
  - In `App.tsx`: `mode` state (initialised from `localStorage["studi0trace.view"]`, `"split"` by default, saved on change) and `viewerRef: RefObject<ViewerHandle>`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/Viewer.test.tsx` (complete):

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { SVG } from "@/test/server";
import { Viewer, type ViewerHandle, type ViewerProps } from "./Viewer";

function setup(patch: Partial<ViewerProps> = {}) {
  const ref = createRef<ViewerHandle>();
  const props: ViewerProps = {
    sourceUrl: "blob:src",
    svg: SVG,
    width: 64,
    height: 64,
    mode: "split",
    onModeChange: vi.fn(),
    busy: null,
    display: { points: false, outlines: false },
    onDisplayChange: vi.fn(),
    layersOpen: false,
    onToggleLayers: vi.fn(),
    ...patch,
  };
  const utils = render(<Viewer ref={ref} {...props} />);
  return { ...utils, ref, props };
}

const zoomLevel = () => screen.getByRole("button", { name: "Zoom level" });
const viewport = () => screen.getByTestId("viewport");

describe("Viewer", () => {
  it("split shows the source and the vector, labels both, and has a keyboard divider", () => {
    setup();
    expect(screen.getByAltText("Source raster")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Vector result" })).toBeInTheDocument();
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(screen.getByText("Vector")).toBeInTheDocument();
    const divider = screen.getByRole("separator", { name: /comparison divider/i });
    expect(divider).toHaveAttribute("aria-valuenow", "50");
    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(divider).toHaveAttribute("aria-valuenow", "52");
  });

  it("the mode is the caller's: the tabs ask for a change, the prop decides", () => {
    const { props, rerender } = setup();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Vector" }));
    expect(props.onModeChange).toHaveBeenCalledWith("vector");
    rerender(<Viewer {...props} mode="vector" />);
    expect(screen.queryByAltText("Source raster")).toBeNull();
    rerender(<Viewer {...props} mode="side" />);
    expect(screen.getByAltText("Source raster")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Side by side" })).toHaveAttribute("aria-selected", "true");
  });

  it("overlay has an opacity slider; errors and the busy bar show", () => {
    setup({ mode: "overlay", busy: "Tracing…", errorMessage: "Vexel failed: nope" });
    expect(screen.getByLabelText("Vector opacity")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Vexel failed");
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("the handle zooms: actual size, in, out", () => {
    const { ref } = setup();
    act(() => ref.current!.actualSize());
    expect(zoomLevel()).toHaveTextContent("100%");
    act(() => ref.current!.zoomIn());
    expect(zoomLevel()).toHaveTextContent("125%");
    act(() => ref.current!.zoomOut());
    act(() => ref.current!.zoomOut());
    expect(zoomLevel()).toHaveTextContent("80%");
  });

  it("two-finger scroll pans and a pinch zooms", () => {
    setup();
    const img = screen.getByAltText("Source raster");
    const before = img.style.transform;
    act(() => {
      viewport().dispatchEvent(new WheelEvent("wheel", { deltaX: 30, deltaY: 10, bubbles: true, cancelable: true }));
    });
    expect(img.style.transform).not.toBe(before);
    expect(zoomLevel()).toHaveTextContent("100%");
    act(() => {
      viewport().dispatchEvent(new WheelEvent("wheel", { deltaY: -50, ctrlKey: true, bubbles: true, cancelable: true }));
    });
    expect(zoomLevel()).not.toHaveTextContent("100%");
  });

  it("the zoom tool zooms in where clicked, and out with Option", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Zoom tool" }));
    fireEvent.pointerDown(viewport(), { button: 0, pointerId: 1, clientX: 10, clientY: 10 });
    expect(zoomLevel()).toHaveTextContent("200%");
    fireEvent.pointerDown(viewport(), { button: 0, pointerId: 1, clientX: 10, clientY: 10, altKey: true });
    expect(zoomLevel()).toHaveTextContent("100%");
  });

  it("split leaves the vector side empty until a vector exists", () => {
    const { props, rerender } = setup({ svg: undefined });
    expect(screen.getByTestId("source-pane")).toHaveStyle({ clipPath: "inset(0 50% 0 0)" });
    rerender(<Viewer {...props} svg={SVG} />);
    expect(screen.getByRole("img", { name: "Vector result" })).toBeInTheDocument();
  });

  it("a failure is a persistent error with Try Again, and its click is not swallowed by panning", () => {
    const onRetry = vi.fn();
    setup({ svg: undefined, errorMessage: "The trace crashed (signal 9)", onRetry });
    const button = screen.getByRole("button", { name: /try again/i });
    fireEvent.pointerDown(button, { button: 0, pointerId: 1 });
    const img = screen.getByAltText("Source raster");
    const before = img.style.transform;
    fireEvent.pointerMove(button, { clientX: 120, clientY: 40, pointerId: 1 });
    expect(img.style.transform).toBe(before);
    fireEvent.click(button);
    expect(onRetry).toHaveBeenCalled();
    expect(screen.queryByText("No vector yet")).toBeNull();
  });

  it("while busy it names the phase", () => {
    setup({ svg: undefined, busy: "Queued…" });
    expect(screen.getByText("Queued…")).toBeInTheDocument();
  });

  it("the layers button and the display toggles are the caller's", () => {
    const { props } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Layers" }));
    fireEvent.click(screen.getByRole("button", { name: "Show anchor points" }));
    expect(props.onToggleLayers).toHaveBeenCalledOnce();
    expect(props.onDisplayChange).toHaveBeenCalledWith({ points: true });
  });
});
```

Run: `cd frontend && npm install @radix-ui/react-dropdown-menu@^2.1.15 && npx vitest run src/components/Viewer.test.tsx`
Expected: FAIL (`./Viewer` does not exist).

- [ ] **Step 2: Write the toolbar**

`frontend/src/components/ViewerToolbar.tsx`:

```tsx
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Tabs from "@radix-ui/react-tabs";
import { ChevronDown, CircleDot, Columns2, Hand, Layers, Layers2, Maximize, PenTool, Search, Spline, SplitSquareHorizontal } from "lucide-react";

import { formatPercent } from "@/lib/format";
import type { ViewMode } from "@/platform/types";

export type Tool = "pan" | "zoom";
export const ZOOM_LEVELS = [0.5, 1, 2, 4];

const MODES: { value: ViewMode; label: string; icon: typeof Columns2 }[] = [
  { value: "split", label: "Split", icon: SplitSquareHorizontal },
  { value: "side", label: "Side by side", icon: Columns2 },
  { value: "overlay", label: "Overlay", icon: Layers2 },
  { value: "vector", label: "Vector", icon: Spline },
];

export interface ViewerToolbarProps {
  tool: Tool;
  onTool: (tool: Tool) => void;
  scale: number;
  onZoomTo: (scale: number | "fit") => void;
  mode: ViewMode;
  onMode: (mode: ViewMode) => void;
  overlay: number;
  onOverlay: (opacity: number) => void;
  hasVector: boolean;
  display: { points: boolean; outlines: boolean };
  onDisplayChange: (patch: { points?: boolean; outlines?: boolean }) => void;
  layersOpen: boolean;
  onToggleLayers: () => void;
}

const group = "flex items-center gap-0.5";
const divider = <span aria-hidden="true" className="mx-1 h-4 w-px bg-border" />;

/** The floating toolbar at the foot of the viewer. */
export function ViewerToolbar(p: ViewerToolbarProps) {
  return (
    <div data-overlay-ui role="toolbar" aria-label="View" className="absolute bottom-3 left-1/2 z-30 flex -translate-x-1/2 items-center rounded-xl border bg-popover/90 p-1 shadow-elevated backdrop-blur-xl">
      <div className={group}>
        <button type="button" className="mac-icon" aria-label="Hand tool" aria-pressed={p.tool === "pan"} title="Hand: drag to pan (or hold Space)" onClick={() => p.onTool("pan")}>
          <Hand className="h-4 w-4" aria-hidden="true" />
        </button>
        <button type="button" className="mac-icon" aria-label="Zoom tool" aria-pressed={p.tool === "zoom"} title="Zoom: click to zoom in, Option-click to zoom out" onClick={() => p.onTool("zoom")}>
          <Search className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      {divider}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button type="button" aria-label="Zoom level" className="tabular inline-flex h-7 min-w-[4.5rem] items-center justify-center gap-1 rounded-md px-2 text-[12px] font-medium hover:bg-accent">
            {formatPercent(p.scale)}
            <ChevronDown className="h-3 w-3 opacity-60" aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content side="top" sideOffset={6} className="mac-menu">
            <DropdownMenu.Item className="mac-menu-item" onSelect={() => p.onZoomTo("fit")}>
              Zoom to Fit
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="mac-menu-separator" />
            {ZOOM_LEVELS.map((z) => (
              <DropdownMenu.Item key={z} className="mac-menu-item" onSelect={() => p.onZoomTo(z)}>
                {formatPercent(z)}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      <button type="button" className="mac-icon" aria-label="Fit to window" title="Zoom to Fit (⌘9)" onClick={() => p.onZoomTo("fit")}>
        <Maximize className="h-4 w-4" aria-hidden="true" />
      </button>
      {divider}
      <Tabs.Root value={p.mode} onValueChange={(v) => p.onMode(v as ViewMode)}>
        <Tabs.List aria-label="Compare" className="inline-flex h-7 items-center rounded-md bg-muted p-0.5">
          {MODES.map((m) => (
            <Tabs.Trigger
              key={m.value}
              value={m.value}
              aria-label={m.label}
              title={m.label}
              className="inline-flex h-6 w-7 items-center justify-center rounded-[5px] text-muted-foreground transition-colors data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm"
            >
              <m.icon className="h-3.5 w-3.5" aria-hidden="true" />
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
      {p.mode === "overlay" && (
        <input type="range" min={0} max={1} step={0.05} value={p.overlay} onChange={(e) => p.onOverlay(Number(e.target.value))} aria-label="Vector opacity" className="ml-2 w-20" style={{ accentColor: "var(--accent-mac)" }} />
      )}
      {p.hasVector && (
        <>
          {divider}
          <div className={group}>
            <button type="button" className="mac-icon" aria-label="Show anchor points" aria-pressed={p.display.points} title="Anchor points" onClick={() => p.onDisplayChange({ points: !p.display.points })}>
              <CircleDot className="h-4 w-4" aria-hidden="true" />
            </button>
            <button type="button" className="mac-icon" aria-label="Show outlines" aria-pressed={p.display.outlines} title="Outlines" onClick={() => p.onDisplayChange({ outlines: !p.display.outlines })}>
              <PenTool className="h-4 w-4" aria-hidden="true" />
            </button>
            <button type="button" className="mac-icon" aria-label="Layers" aria-pressed={p.layersOpen} title="Layers" onClick={p.onToggleLayers}>
              <Layers className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
```

Add the menu styles to `@layer components` in `frontend/src/styles.css` (Task 10 and 11 use them too):

```css
  .mac-menu {
    @apply z-50 min-w-[11rem] rounded-lg border bg-popover/95 p-1 text-[13px] text-popover-foreground shadow-elevated backdrop-blur-xl;
  }
  .mac-menu-item {
    @apply flex h-6 cursor-default select-none items-center gap-2 rounded-[5px] px-2 outline-none data-[disabled]:opacity-40;
  }
  .mac-menu-item[data-highlighted] {
    background: var(--accent-mac);
    color: white;
  }
  .mac-menu-separator {
    @apply mx-1 my-1 h-px bg-border;
  }
  .mac-menu-shortcut {
    @apply ml-auto pl-4 text-[12px] opacity-60;
  }
```

- [ ] **Step 3: Write the viewer**

`git mv frontend/src/components/Canvas.tsx frontend/src/components/Viewer.tsx`, then make it (complete):

```tsx
import { AlertCircle, ChevronLeft, ChevronRight, Loader2, RotateCw } from "lucide-react";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import type { ViewMode } from "@/platform/types";
import { ViewerToolbar, type Tool } from "./ViewerToolbar";

export interface ViewerHandle {
  zoomIn(): void;
  zoomOut(): void;
  actualSize(): void;
  fit(): void;
}

export interface ViewerProps {
  sourceUrl: string;
  svg: string | undefined;
  width: number;
  height: number;
  mode: ViewMode;
  onModeChange: (mode: ViewMode) => void;
  /** What the wait is for ("Queued…", "Tracing…") while a job runs; null when none does. */
  busy: string | null;
  errorMessage?: string;
  onRetry?: () => void;
  display: { points: boolean; outlines: boolean };
  onDisplayChange: (patch: { points?: boolean; outlines?: boolean }) => void;
  layersOpen: boolean;
  onToggleLayers: () => void;
  /** Drawn in the vector's own coordinates, over everything. */
  marks?: (scale: number) => ReactNode;
  /** Rendered inside the viewport, e.g. the layer inspector. */
  panel?: ReactNode;
}

interface Transform {
  scale: number;
  x: number;
  y: number;
}

const MIN_SCALE = 0.05;
const MAX_SCALE = 32;
const STEP = 1.25;
const PAD = 32;

const isTyping = (target: EventTarget | null) => target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

export const Viewer = forwardRef<ViewerHandle, ViewerProps>(function Viewer(props, ref) {
  const { sourceUrl, svg, width, height, mode, onModeChange, busy, errorMessage, onRetry, display, onDisplayChange, layersOpen, onToggleLayers, marks, panel } = props;
  const [split, setSplit] = useState(0.5);
  const [overlay, setOverlay] = useState(0.7);
  const [tool, setTool] = useState<Tool>("pan");
  const [spaceHeld, setSpaceHeld] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [t, setT] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  const [fitted, setFitted] = useState(false);

  useLayoutEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // the pane an image occupies: half the viewport side by side
  const paneW = mode === "side" ? size.w / 2 : size.w;
  const fitTransform = useCallback((): Transform => {
    if (!paneW || !size.h || !width || !height) return { scale: 1, x: 0, y: 0 };
    const scale = Math.min((paneW - PAD * 2) / width, (size.h - PAD * 2) / height);
    return { scale, x: (paneW - width * scale) / 2, y: (size.h - height * scale) / 2 };
  }, [paneW, size.h, width, height]);

  useEffect(() => {
    if (!fitted && paneW && size.h && width && height) {
      setT(fitTransform());
      setFitted(true);
    }
  }, [fitted, paneW, size.h, width, height, fitTransform]);
  useEffect(() => setFitted(false), [sourceUrl, mode, width, height]);

  const zoomAt = useCallback((factor: number, cx: number, cy: number) => {
    setT((prev) => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
      const k = scale / prev.scale;
      return { scale, x: cx - (cx - prev.x) * k, y: cy - (cy - prev.y) * k };
    });
  }, []);
  const zoomTo = useCallback(
    (scale: number | "fit") => setT(scale === "fit" ? fitTransform() : { scale, x: (paneW - width * scale) / 2, y: (size.h - height * scale) / 2 }),
    [fitTransform, paneW, size.h, width, height],
  );

  useImperativeHandle(
    ref,
    () => ({
      zoomIn: () => zoomAt(STEP, paneW / 2, size.h / 2),
      zoomOut: () => zoomAt(1 / STEP, paneW / 2, size.h / 2),
      actualSize: () => zoomTo(1),
      fit: () => zoomTo("fit"),
    }),
    [zoomAt, zoomTo, paneW, size.h],
  );

  // The trackpad: two-finger scroll pans; a pinch zooms (ctrl+wheel in Chromium, gesture events in WebKit).
  // Added by hand because React's wheel listener is passive and cannot stop the page zooming.
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const local = (e: { clientX: number; clientY: number }) => {
      const r = el.getBoundingClientRect();
      return [(e.clientX - r.left) % (paneW || 1), e.clientY - r.top] as const;
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        const [cx, cy] = local(e);
        zoomAt(Math.exp(-e.deltaY * 0.01), cx, cy);
      } else {
        setT((p) => ({ ...p, x: p.x - e.deltaX, y: p.y - e.deltaY }));
      }
    };
    let last = 1;
    const gestureStart = (e: Event) => {
      e.preventDefault();
      last = 1;
    };
    const gestureChange = (e: Event) => {
      e.preventDefault();
      const g = e as Event & { scale: number; clientX: number; clientY: number };
      const [cx, cy] = local(g);
      zoomAt(g.scale / last, cx, cy);
      last = g.scale;
    };
    el.addEventListener("wheel", wheel, { passive: false });
    el.addEventListener("gesturestart", gestureStart);
    el.addEventListener("gesturechange", gestureChange);
    return () => {
      el.removeEventListener("wheel", wheel);
      el.removeEventListener("gesturestart", gestureStart);
      el.removeEventListener("gesturechange", gestureChange);
    };
  }, [paneW, zoomAt]);

  // holding Space is the hand, whatever the tool
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !isTyping(e.target)) {
        e.preventDefault();
        setSpaceHeld(true);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") setSpaceHeld(false);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);
  const panning = tool === "pan" || spaceHeld;

  const drag = useRef<{ x: number; y: number; tx: number; ty: number; kind: "pan" | "split" } | null>(null);
  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    // controls painted over the viewer keep their clicks: capturing the pointer here would retarget them
    if ((e.target as HTMLElement).closest("[data-overlay-ui]")) return;
    if (!panning) {
      const r = viewport.current!.getBoundingClientRect();
      zoomAt(e.altKey ? 0.5 : 2, (e.clientX - r.left) % (paneW || 1), e.clientY - r.top);
      return;
    }
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, kind: "pan" };
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "split") {
      const rect = viewport.current!.getBoundingClientRect();
      setSplit(Math.min(0.98, Math.max(0.02, (e.clientX - rect.left) / (rect.width || 1))));
    } else {
      setT((prev) => ({ ...prev, x: d.tx + (e.clientX - d.x), y: d.ty + (e.clientY - d.y) }));
    }
  };
  const onPointerUp = () => (drag.current = null);
  const startSplit = (e: ReactPointerEvent) => {
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y, kind: "split" };
  };

  const imgStyle = useMemo(() => ({ width, height, transform: `translate(${t.x}px, ${t.y}px) scale(${t.scale})`, transformOrigin: "0 0" as const }), [t, width, height]);

  // the checkerboard is under the image only; the panel around it stays the panel
  const board = <div aria-hidden="true" className="checker absolute left-0 top-0 rounded-[2px] shadow-sm" style={imgStyle} />;
  const source = <img src={sourceUrl} alt="Source raster" draggable={false} className="absolute left-0 top-0 max-w-none select-none" style={{ ...imgStyle, imageRendering: t.scale > 3 ? "pixelated" : "auto" }} />;
  const marksNode = marks ? (
    <div className="pointer-events-none absolute left-0 top-0" style={imgStyle}>
      {marks(t.scale)}
    </div>
  ) : null;
  const vector = svg ? (
    <div aria-label="Vector result" role="img" className={`absolute left-0 top-0 [&>svg]:block [&>svg]:h-full [&>svg]:w-full ${busy ? "opacity-60" : ""}`} style={imgStyle} dangerouslySetInnerHTML={{ __html: svg }} />
  ) : null;
  const chip = (text: string, where: string) => <span className={`pointer-events-none absolute top-3 z-20 rounded-full bg-popover/85 px-2.5 py-0.5 text-[11px] font-medium shadow-sm backdrop-blur ${where}`}>{text}</span>;

  return (
    <section aria-label="Canvas" className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      {busy && svg && (
        <div className="absolute inset-x-0 top-0 z-30 h-0.5 overflow-hidden bg-muted" role="progressbar" aria-label="Tracing">
          <div className="h-full w-1/3 animate-[slide_1.1s_ease-in-out_infinite]" style={{ background: "var(--accent-mac)" }} />
        </div>
      )}
      <div
        ref={viewport}
        data-testid="viewport"
        data-mode={mode}
        className={`relative min-h-[16rem] flex-1 touch-none select-none overflow-hidden ${panning ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in"}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {mode === "side" ? (
          <>
            <div data-testid="source-pane" className="absolute inset-y-0 left-0 w-1/2 overflow-hidden">
              {board}
              {source}
            </div>
            <div className="absolute inset-y-0 right-0 w-1/2 overflow-hidden border-l">
              {board}
              {vector}
              {marksNode}
            </div>
            {chip("Original", "left-1/4 -translate-x-1/2")}
            {chip("Vector", "left-3/4 -translate-x-1/2")}
          </>
        ) : (
          <>
            {board}
            {/* In split the source is clipped to its side: running under the empty half it would read as a finished trace. */}
            {mode === "split" ? (
              <div data-testid="source-pane" className="absolute inset-0" style={{ clipPath: `inset(0 ${(1 - split) * 100}% 0 0)` }}>
                {source}
              </div>
            ) : (
              mode !== "vector" && source
            )}
            {mode === "split" && (
              <div className="absolute inset-0" style={{ clipPath: `inset(0 0 0 ${split * 100}%)` }}>
                {vector}
                {marksNode}
              </div>
            )}
            {mode === "overlay" && (
              <>
                <div className="absolute inset-0" style={{ opacity: overlay }}>
                  {vector}
                </div>
                {marksNode}
              </>
            )}
            {mode === "vector" && (
              <>
                {vector}
                {marksNode}
              </>
            )}
            {mode === "split" && (
              <>
                <div
                  role="separator"
                  aria-label="Comparison divider"
                  aria-valuenow={Math.round(split * 100)}
                  aria-orientation="vertical"
                  tabIndex={0}
                  onPointerDown={startSplit}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowLeft") setSplit((s) => Math.max(0.02, s - 0.02));
                    if (e.key === "ArrowRight") setSplit((s) => Math.min(0.98, s + 0.02));
                  }}
                  className="absolute inset-y-0 z-10 w-6 -translate-x-1/2 cursor-col-resize"
                  style={{ left: `${split * 100}%` }}
                >
                  <div className="mx-auto h-full w-px bg-foreground/50" />
                  <div className="absolute left-1/2 top-1/2 flex h-10 w-10 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border bg-popover/90 shadow-elevated backdrop-blur">
                    <ChevronLeft className="-mr-1 h-3.5 w-3.5" aria-hidden="true" />
                    <ChevronRight className="-ml-1 h-3.5 w-3.5" aria-hidden="true" />
                  </div>
                </div>
                {chip("Original", "left-3")}
                {chip("Vector", "right-3")}
              </>
            )}
            {mode === "overlay" && chip("Overlay", "left-1/2 -translate-x-1/2")}
            {mode === "vector" && chip("Vector", "left-1/2 -translate-x-1/2")}
          </>
        )}

        {panel}

        {svg && errorMessage && (
          <div className="absolute inset-x-0 bottom-16 mx-auto w-fit max-w-md rounded-lg bg-destructive/90 px-3 py-2 text-[13px] text-destructive-foreground shadow-md" role="alert">
            {errorMessage}
          </div>
        )}

        {!svg && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center p-6">
            {errorMessage ? (
              <div role="alert" data-overlay-ui className="pointer-events-auto max-w-sm rounded-xl border bg-popover/95 p-4 text-center shadow-elevated backdrop-blur">
                <AlertCircle className="mx-auto h-5 w-5 text-destructive" aria-hidden="true" />
                <p className="mt-2 text-[13px] font-semibold">Tracing failed</p>
                <p className="mt-1 text-[13px] text-muted-foreground">{errorMessage}</p>
                {onRetry && (
                  <button type="button" className="mac-button mt-3" onClick={onRetry}>
                    <RotateCw className="h-4 w-4" aria-hidden="true" />
                    Try Again
                  </button>
                )}
              </div>
            ) : busy ? (
              <div className="flex items-center gap-2.5 rounded-full border bg-popover/90 px-4 py-2 shadow-sm backdrop-blur" aria-live="polite">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />
                <p className="text-[13px] font-medium">{busy}</p>
              </div>
            ) : (
              <p className="rounded-full bg-popover/80 px-3 py-1 text-[13px] text-muted-foreground">Press Generate Vector (⌘↩) to trace</p>
            )}
          </div>
        )}
      </div>
      <ViewerToolbar
        tool={tool}
        onTool={setTool}
        scale={t.scale}
        onZoomTo={zoomTo}
        mode={mode}
        onMode={onModeChange}
        overlay={overlay}
        onOverlay={setOverlay}
        hasVector={!!svg}
        display={display}
        onDisplayChange={onDisplayChange}
        layersOpen={layersOpen}
        onToggleLayers={onToggleLayers}
      />
    </section>
  );
});
```

(`git mv frontend/src/components/Canvas.test.tsx frontend/src/components/Viewer.test.tsx` and replace its content with Step 1's.)

- [ ] **Step 4: Use it in the app**

In `frontend/src/App.tsx`, in `Workspace`:

```tsx
const VIEW_KEY = "studi0trace.view";
const MODES: ViewMode[] = ["split", "side", "overlay", "vector"];
```

```tsx
  const [mode, setModeState] = useState<ViewMode>(() => {
    const saved = localStorage.getItem(VIEW_KEY) as ViewMode | null;
    return saved && MODES.includes(saved) ? saved : "split";
  });
  const setMode = useCallback((m: ViewMode) => {
    setModeState(m);
    try {
      localStorage.setItem(VIEW_KEY, m);
    } catch {
      /* a remembered convenience, nothing more */
    }
  }, []);
  const viewerRef = useRef<ViewerHandle>(null);
  const busy = item?.job ? (item.job.phase === "queued" ? "Queued…" : item.job.key === "auto" ? `Trying ${catalog.presets.filter((p) => p.auto_candidate).length} presets…` : "Tracing…") : null;
```

and replace the `<Canvas …/>` element with:

```tsx
    <Viewer
      ref={viewerRef}
      sourceUrl={item.image.previewUrl}
      svg={layers.exportSvg}
      width={item.image.width}
      height={item.image.height}
      mode={mode}
      onModeChange={setMode}
      busy={busy}
      errorMessage={item.error?.message}
      onRetry={() => lib.generate(item.image.id)}
      display={{ points: layers.state.points, outlines: layers.state.outlines }}
      onDisplayChange={layers.patch}
      layersOpen={layers.state.open}
      onToggleLayers={() => layers.patch({ open: !layers.state.open })}
      marks={layers.doc ? (scale) => <InspectorOverlay doc={layers.doc!} state={layers.liveState} scale={scale} /> : undefined}
      panel={layers.doc && layers.state.open ? <Inspector doc={layers.doc} bytes={layers.exportSvg?.length ?? 0} elapsedMs={answer?.elapsedMs} engineLabel={catalog.engine.label} edited={layers.dropped.size > 0} state={layers.liveState} onChange={layers.patch} /> : undefined}
    />
```

(import `Viewer`, `type ViewerHandle` from `./components/Viewer`, `type ViewMode` from `./platform`, `useRef`; drop the `Canvas` import).

- [ ] **Step 5: Run everything**

Run: `cd frontend && npx vitest run src/components/Viewer.test.tsx && npm run test:run && npm run build`
Expected: 10 viewer tests pass; the suite and the build pass.

- [ ] **Step 6: Look at it**

In the browser harness, with the Logo sample traced: the four modes; the chips; the split handle as a round 40 px button with two chevrons; the floating toolbar centred at the foot; the checkerboard only under the image; scroll and ctrl-scroll (pinch in the app) pan and zoom; light and dark.

- [ ] **Step 7: Commit**

```bash
git add -A frontend
git commit -m "frontend: the viewer: floating toolbar, Mac gestures, a zoom tool, chips, and a handle for the menus

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The Vectorize panel and export

The right panel of the concept: "Vectorize", the presets as radio cards (Auto first, More Styles folded), Advanced Options, a stats line, **Generate Vector** / **Update Vector** / **Cancel · 12 s**, and **Export SVG** with its menu. Export goes through the platform (a save panel or beside the original in the app; a download in a browser).

**Files:**
- Create: `frontend/src/components/PresetCards.tsx`, `TraceButton.tsx`, `ExportMenu.tsx`, `VectorizePanel.tsx`, `frontend/src/hooks/useExports.ts`
- Test: `frontend/src/components/PresetCards.test.tsx`, `TraceButton.test.tsx`, `ExportMenu.test.tsx`, `VectorizePanel.test.tsx`
- Modify: `frontend/src/App.tsx` (the panel and the exports), `frontend/src/App.test.tsx` (Auto is a radio now)
- Delete (`git rm`): `frontend/src/components/Presets.tsx`, `Presets.test.tsx`, `Actions.tsx`

**Interfaces:**
- Consumes: `ImageItem`, `Catalog`, `traceKey`, `needsUpdate`, `shownAnswer`, `Library` (Task 7); `Platform.exportFile/exportAll/copyText/reveal`, `Settings` (Task 6); `ParamPanel`, `ParamSpec`; `svgToPngBlob`, `baseName` (`@/lib/raster`); `formatBytes`, `formatInt`, `formatMs`.
- Produces:
  - `PresetCards({ presets, defaults, values, active, auto, autoRunning, onPick, disabled? })`, and moved here from `Presets.tsx`: `activePreset(presets, values, defaults)`, `autoChoice(auto, presets)`; new: `firstSentence(text)`, `PRESET_ICONS`.
  - `TraceButton({ item, candidates, onGenerate, onCancel })`
  - `ExportMenu({ canExport, anyVector, onExport(kind: "svg" | "png", scale: number), onCopy, onExportAll })`
  - `VectorizePanel({ item, catalog, specs, invalidField, onPick, onParam, onGenerate, onCancel, exportMenu })`
  - `useExports(state: LibraryState, item: ImageItem | null, svg: string | undefined, settings: Settings) => { exportImage(kind, scale), exportAll(), copySvg() }`

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/PresetCards.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AutoResult } from "@/lib/api";
import { VEXEL, VEXEL_PRESETS } from "@/test/server";
import { activePreset, autoChoice, firstSentence, PresetCards } from "./PresetCards";

const defaults = VEXEL.defaults;
const auto: AutoResult = {
  engine: "vexel",
  pick: "logo",
  reason: "the cleanest at the same fidelity",
  candidates: [
    { preset: "balanced", label: "Balanced", svg: "<svg/>", scores: { delta_e: 0.25, edge_f1: 0.9, artifact_index: 3.2, clean: false, issues: ["2 pinholes"], shapes: 6, pinholes: 2, slivers: 0, wobble: 0, inflections: 0, uneven_rects: 0 } },
    { preset: "logo", label: "Logo & icon", svg: "<svg/>", scores: { delta_e: 0.31, edge_f1: 0.9, artifact_index: 0, clean: true, issues: [], shapes: 3, pinholes: 0, slivers: 0, wobble: 0, inflections: 0, uneven_rects: 0 } },
  ],
};

describe("PresetCards", () => {
  it("is a radio group, Auto first, the styles Auto never picks folded away", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning={false} onPick={vi.fn()} />);
    const group = screen.getByRole("radiogroup", { name: "Preset" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false", "false"]);
    expect(radios[0]).toHaveAccessibleName(/^Auto/);
    expect(screen.queryByRole("radio", { name: /^Flat & poster/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More Styles" }));
    expect(screen.getByRole("radio", { name: /^Flat & poster/ })).toBeInTheDocument();
  });

  it("says the first sentence of each description and hands back the preset picked", () => {
    const onPick = vi.fn();
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning={false} onPick={onPick} />);
    expect(screen.getByText("Gradients, shadows, strokes and overlaps all reconstructed.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /^Logo & icon/ }));
    expect(onPick).toHaveBeenCalledWith(VEXEL_PRESETS[2]);
    fireEvent.keyDown(screen.getByRole("radio", { name: /^Auto/ }), { key: "ArrowDown" });
    expect(onPick).toHaveBeenLastCalledWith(VEXEL_PRESETS[1]);
  });

  it("after Auto: what it chose and why, each candidate's issues, and the pick marked", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={auto} autoRunning={false} onPick={vi.fn()} />);
    expect(screen.getByLabelText("Auto chose Logo & icon — the cleanest at the same fidelity")).toBeInTheDocument();
    expect(within(screen.getByRole("radio", { name: /^Balanced/ })).getByText("2 pinholes")).toBeInTheDocument();
    expect(within(screen.getByRole("radio", { name: /^Logo & icon/ })).getByText("Auto's pick")).toBeInTheDocument();
    expect(within(screen.getByRole("radio", { name: /^Logo & icon/ })).getByText("Clean")).toBeInTheDocument();
  });

  it("while Auto runs it says so", () => {
    render(<PresetCards presets={VEXEL_PRESETS} defaults={defaults} values={defaults} active="auto" auto={null} autoRunning onPick={vi.fn()} />);
    expect(screen.getByText("Trying 2 presets on your image…")).toBeInTheDocument();
  });

  it("the helpers", () => {
    expect(firstSentence("One thing. Another thing.")).toBe("One thing.");
    expect(firstSentence("No full stop")).toBe("No full stop");
    expect(activePreset(VEXEL_PRESETS, { detail: 10, min_region: 16 }, defaults)).toBe("logo");
    expect(activePreset(VEXEL_PRESETS, { detail: 7, min_region: 8 }, defaults)).toBeNull();
    expect(autoChoice(auto, VEXEL_PRESETS)).toBe("chose Logo & icon — the cleanest at the same fidelity");
  });
});
```

`frontend/src/components/TraceButton.test.tsx`:

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { paramsKey } from "@/lib/schema";
import type { ImageItem } from "@/state/library";
import { TraceButton } from "./TraceButton";

const base: ImageItem = {
  image: { id: "a", name: "a.png", path: null, width: 4, height: 4, format: "PNG", previewUrl: "blob:a" },
  preset: "balanced",
  params: { detail: 6 },
  traces: {},
  shown: null,
  job: null,
  error: null,
  auto: null,
};
const answer = { svg: "<svg/>", elapsedMs: 900, stats: {} };

describe("TraceButton", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] }));
  afterEach(() => vi.useRealTimers());

  it("generates an untraced image", () => {
    const onGenerate = vi.fn();
    render(<TraceButton item={base} candidates={4} onGenerate={onGenerate} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate Vector/ }));
    expect(onGenerate).toHaveBeenCalledOnce();
  });

  it("is up to date when the trace on screen is the settings', and Update when they moved", () => {
    const key = paramsKey({ detail: 6 });
    const { rerender } = render(<TraceButton item={{ ...base, traces: { [key]: answer }, shown: key }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Generate Vector/ })).toBeDisabled();
    expect(screen.getByText("Up to date")).toBeInTheDocument();
    rerender(<TraceButton item={{ ...base, params: { detail: 9 }, traces: { [key]: answer }, shown: key }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Update Vector/ })).toBeEnabled();
  });

  it("while a job runs: its phase, the seconds, and Cancel", () => {
    const onCancel = vi.fn();
    const started = Date.now();
    render(<TraceButton item={{ ...base, preset: "auto", job: { id: "j", key: "auto", startedAt: started, phase: "tracing" } }} candidates={4} onGenerate={vi.fn()} onCancel={onCancel} />);
    expect(screen.getByText("Trying 4 presets…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel · 0 s" })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3000));
    fireEvent.click(screen.getByRole("button", { name: "Cancel · 3 s" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("a queued job says so", () => {
    render(<TraceButton item={{ ...base, job: { id: "j", key: "x", startedAt: Date.now(), phase: "queued" } }} candidates={4} onGenerate={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText("Queued…")).toBeInTheDocument();
  });
});
```

`frontend/src/components/ExportMenu.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ExportMenu } from "./ExportMenu";

function open() {
  fireEvent.keyDown(screen.getByRole("button", { name: "More export options" }), { key: "Enter" });
}

describe("ExportMenu", () => {
  it("exports the SVG, and is off with nothing to export", () => {
    const onExport = vi.fn();
    const { rerender } = render(<ExportMenu canExport anyVector onExport={onExport} onCopy={vi.fn()} onExportAll={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Export SVG/ }));
    expect(onExport).toHaveBeenCalledWith("svg", 1);
    rerender(<ExportMenu canExport={false} anyVector={false} onExport={onExport} onCopy={vi.fn()} onExportAll={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Export SVG/ })).toBeDisabled();
  });

  it("offers PNG at three sizes, Copy SVG and Export All", () => {
    const onExport = vi.fn();
    const onCopy = vi.fn();
    const onExportAll = vi.fn();
    render(<ExportMenu canExport anyVector onExport={onExport} onCopy={onCopy} onExportAll={onExportAll} />);
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /PNG at 2×/ }));
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /Copy SVG/ }));
    open();
    fireEvent.click(screen.getByRole("menuitem", { name: /Export All/ }));
    expect(onExport).toHaveBeenCalledWith("png", 2);
    expect(onCopy).toHaveBeenCalledOnce();
    expect(onExportAll).toHaveBeenCalledOnce();
  });
});
```

`frontend/src/components/VectorizePanel.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { paramsKey, specsFor } from "@/lib/schema";
import type { ImageItem } from "@/state/library";
import { VEXEL, VEXEL_PRESETS } from "@/test/server";
import { VectorizePanel } from "./VectorizePanel";

const key = paramsKey({ detail: 6, min_region: 8 });
const item: ImageItem = {
  image: { id: "a", name: "a.png", path: null, width: 4, height: 4, format: "PNG", previewUrl: "blob:a" },
  preset: "balanced",
  params: { detail: 6, min_region: 8 },
  traces: { [key]: { svg: "<svg/>", elapsedMs: 1530, stats: { paths: 16, nodes: 208, bytes: 4544 } } },
  shown: key,
  job: null,
  error: null,
  auto: null,
};

describe("VectorizePanel", () => {
  it("heads the panel, folds the advanced options and states what the trace is", () => {
    const onParam = vi.fn();
    render(
      <VectorizePanel item={item} catalog={{ engine: VEXEL, presets: VEXEL_PRESETS }} specs={specsFor(VEXEL)} invalidField={null} onPick={vi.fn()} onParam={onParam} onGenerate={vi.fn()} onCancel={vi.fn()} exportMenu={<span>export</span>} />,
    );
    expect(screen.getByRole("heading", { name: "Vectorize" })).toBeInTheDocument();
    expect(screen.getByText("16 shapes · 208 nodes · 4.4 KB · 1.5 s")).toBeInTheDocument();
    expect(screen.queryByText("Shapes")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Advanced Options" }));
    expect(screen.getByText("Shapes")).toBeInTheDocument();
    expect(screen.getByText("export")).toBeInTheDocument();
  });
});
```

Run: `cd frontend && npx vitest run src/components/PresetCards.test.tsx src/components/TraceButton.test.tsx src/components/ExportMenu.test.tsx src/components/VectorizePanel.test.tsx`
Expected: FAIL (modules missing).

- [ ] **Step 2: Write the components**

`frontend/src/components/PresetCards.tsx`:

```tsx
import { ChevronRight, Circle, Layers, LayoutGrid, Mountain, Scissors, Shapes, SlidersHorizontal, Sparkles, type LucideIcon } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import type { AutoResult, ParamValues, Preset } from "@/lib/api";

/** An icon per preset id; a preset added later gets the shapes. */
export const PRESET_ICONS: Record<string, LucideIcon> = { auto: Sparkles, balanced: LayoutGrid, logo: Mountain, detailed: SlidersHorizontal, dense: Circle, flat: Layers, cutfile: Scissors };

export function firstSentence(text: string): string {
  const m = /^(.+?[.!?])(\s|$)/.exec(text.trim());
  return m ? m[1] : text.trim();
}

/** Which fixed preset the values match, if any. Auto has no values of its own, so it never matches. */
export function activePreset(presets: Preset[], values: ParamValues, defaults: ParamValues): string | null {
  for (const p of presets) {
    if (p.kind === "auto") continue;
    const want = { ...defaults, ...p.params };
    if (Object.keys(want).every((k) => String(want[k]) === String(values[k]))) return p.id;
  }
  return null;
}

/** What one Auto run chose, as "chose Logo & icon — the cleanest at the same fidelity". */
export function autoChoice(auto: AutoResult, presets: Preset[]): string {
  if (!auto.pick) return `couldn't choose — ${auto.reason}`;
  const label = presets.find((p) => p.id === auto.pick)?.label ?? auto.candidates.find((c) => c.preset === auto.pick)?.label ?? auto.pick;
  return `chose ${label} — ${auto.reason}`;
}

export interface PresetCardsProps {
  presets: Preset[];
  defaults: ParamValues;
  values: ParamValues;
  /** "auto", a preset's id, or null when the values were set by hand (then whichever preset they match, if any). */
  active: string | null;
  auto: AutoResult | null;
  autoRunning: boolean;
  onPick: (preset: Preset) => void;
  disabled?: boolean;
}

export function PresetCards({ presets, defaults, values, active, auto, autoRunning, onPick, disabled }: PresetCardsProps) {
  const [more, setMore] = useState(false);
  const on = active ?? activePreset(presets, values, defaults);
  const hasAuto = presets.some((p) => p.kind === "auto");
  const leading = presets.filter((p) => !hasAuto || p.kind === "auto" || p.auto_candidate);
  const trailing = hasAuto ? presets.filter((p) => p.kind !== "auto" && !p.auto_candidate) : [];
  const shown = more || trailing.some((p) => p.id === on) ? [...leading, ...trailing] : leading;
  const candidates = presets.filter((p) => p.auto_candidate).length;
  const byId = new Map((auto?.candidates ?? []).map((c) => [c.preset, c]));

  const move = (e: KeyboardEvent, at: number) => {
    const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (step) {
      e.preventDefault();
      const next = shown[Math.max(0, Math.min(shown.length - 1, at + step))];
      if (next) onPick(next);
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      onPick(shown[at]);
    }
  };

  const card = (p: Preset, at: number) => {
    const checked = p.id === on;
    const Icon = PRESET_ICONS[p.id] ?? Shapes;
    const cand = byId.get(p.id);
    let sub = <span className="line-clamp-2">{firstSentence(p.description)}</span>;
    if (p.kind === "auto" && autoRunning) sub = <span>Trying {candidates} presets on your image…</span>;
    else if (p.kind === "auto" && auto) {
      const choice = autoChoice(auto, presets);
      sub = <span aria-label={`Auto ${choice}`}>{choice.charAt(0).toUpperCase() + choice.slice(1)}</span>;
    } else if (cand?.scores) {
      sub = (
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cand.scores.clean ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
          <span className="truncate">{cand.scores.clean ? "Clean" : cand.scores.issues.join(", ")}</span>
        </span>
      );
    } else if (cand?.error) sub = <span className="text-destructive">{cand.error.message}</span>;
    return (
      <div
        key={p.id}
        role="radio"
        aria-checked={checked}
        aria-disabled={disabled || undefined}
        tabIndex={checked || (on === null && at === 0) ? 0 : -1}
        title={`${p.description}\n${p.detail}`}
        onClick={() => !disabled && onPick(p)}
        onKeyDown={(e) => !disabled && move(e, at)}
        className="mac-choice flex items-center gap-3"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-background ring-1 ring-border">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[13px] font-semibold">{p.label}</span>
            {auto?.pick === p.id && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded bg-secondary px-1 text-[10px] font-medium leading-4">
                <span className="dot-brand" aria-hidden="true" />
                Auto's pick
              </span>
            )}
          </span>
          <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground" aria-live={p.kind === "auto" ? "polite" : undefined}>
            {sub}
          </span>
        </span>
        <span className="mac-radio" aria-hidden="true" />
      </div>
    );
  };

  return (
    <section className="space-y-1.5">
      <div role="radiogroup" aria-label="Preset" className="space-y-1.5">
        {shown.map(card)}
      </div>
      {trailing.length > 0 && !trailing.some((p) => p.id === on) && (
        <button type="button" aria-expanded={more} onClick={() => setMore((v) => !v)} className="mac-ghost h-7 px-1.5 text-[12px]">
          <ChevronRight className={`h-3.5 w-3.5 transition-transform ${more ? "rotate-90" : ""}`} aria-hidden="true" />
          More Styles
        </button>
      )}
    </section>
  );
}
```

`frontend/src/components/TraceButton.tsx`:

```tsx
import { Wand2 } from "lucide-react";
import { useEffect, useState } from "react";

import { needsUpdate, traceKey, type ImageItem } from "@/state/library";

export interface TraceButtonProps {
  item: ImageItem;
  /** How many presets Auto tries, for "Trying 4 presets…". */
  candidates: number;
  onGenerate: () => void;
  onCancel: () => void;
}

export function TraceButton({ item, candidates, onGenerate, onCancel }: TraceButtonProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!item.job) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [item.job]);

  if (item.job) {
    const seconds = Math.max(0, Math.floor((now - item.job.startedAt) / 1000));
    const phase = item.job.phase === "queued" ? "Queued…" : item.job.key === "auto" ? `Trying ${candidates} presets…` : "Tracing…";
    return (
      <div className="space-y-1.5">
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground" aria-live="polite">
          <span className={item.job.phase === "queued" ? "inline-block h-1.5 w-1.5 rounded-full bg-warning" : "mac-dot animate-pulse"} aria-hidden="true" />
          {phase}
        </p>
        <button type="button" className="mac-button h-9 w-full" onClick={onCancel} title="Cancel Trace (⌘.)">
          Cancel · {seconds} s
        </button>
      </div>
    );
  }
  const current = item.shown !== null && item.shown === traceKey(item);
  const label = needsUpdate(item) ? "Update Vector" : "Generate Vector";
  return (
    <div className="space-y-1">
      <button type="button" className="mac-primary" disabled={current} onClick={onGenerate} title={`${label} (⌘↩)`}>
        <Wand2 className="h-4 w-4" aria-hidden="true" />
        {label}
      </button>
      {current && <p className="text-center text-[11px] text-muted-foreground">Up to date</p>}
    </div>
  );
}
```

`frontend/src/components/ExportMenu.tsx`:

```tsx
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ChevronDown, Download } from "lucide-react";

export interface ExportMenuProps {
  canExport: boolean;
  anyVector: boolean;
  onExport: (kind: "svg" | "png", scale: number) => void;
  onCopy: () => void;
  onExportAll: () => void;
}

/** Export SVG, with the rest a click away: PNG at three sizes, Copy SVG, Export All. */
export function ExportMenu({ canExport, anyVector, onExport, onCopy, onExportAll }: ExportMenuProps) {
  return (
    <div className="flex">
      <button type="button" className="mac-button h-9 flex-1 rounded-r-none" disabled={!canExport} onClick={() => onExport("svg", 1)} title="Export SVG… (⌘E)">
        <Download className="h-4 w-4" aria-hidden="true" />
        Export SVG
      </button>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button type="button" aria-label="More export options" className="mac-button h-9 rounded-l-none border-l-0 px-2" disabled={!canExport && !anyVector}>
            <ChevronDown className="h-4 w-4" aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={6} className="mac-menu">
            <DropdownMenu.Item className="mac-menu-item" disabled={!canExport} onSelect={() => onExport("svg", 1)}>
              Export SVG…<span className="mac-menu-shortcut">⌘E</span>
            </DropdownMenu.Item>
            {[1, 2, 4].map((s) => (
              <DropdownMenu.Item key={s} className="mac-menu-item" disabled={!canExport} onSelect={() => onExport("png", s)}>
                Export PNG at {s}×…{s === 2 && <span className="mac-menu-shortcut">⇧⌘E</span>}
              </DropdownMenu.Item>
            ))}
            <DropdownMenu.Separator className="mac-menu-separator" />
            <DropdownMenu.Item className="mac-menu-item" disabled={!canExport} onSelect={onCopy}>
              Copy SVG<span className="mac-menu-shortcut">⇧⌘C</span>
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="mac-menu-separator" />
            <DropdownMenu.Item className="mac-menu-item" disabled={!anyVector} onSelect={onExportAll}>
              Export All…<span className="mac-menu-shortcut">⌥⌘E</span>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  );
}
```

(The split button's two halves share one outline: `border-l-0` removes the doubled line between them, not a highlight.)

`frontend/src/components/VectorizePanel.tsx`:

```tsx
import { ChevronRight, Wand2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import type { Preset } from "@/lib/api";
import { formatBytes, formatInt, formatMs } from "@/lib/format";
import type { ParamSpec } from "@/lib/schema";
import { shownAnswer, type Catalog, type ImageItem } from "@/state/library";
import { ParamPanel } from "./ParamPanel";
import { PresetCards } from "./PresetCards";
import { TraceButton } from "./TraceButton";

export interface VectorizePanelProps {
  item: ImageItem;
  catalog: Catalog;
  specs: ParamSpec[];
  invalidField: string | null;
  onPick: (preset: Preset) => void;
  onParam: (name: string, value: unknown) => void;
  onGenerate: () => void;
  onCancel: () => void;
  /** The export button and its menu. */
  exportMenu: ReactNode;
}

export function VectorizePanel({ item, catalog, specs, invalidField, onPick, onParam, onGenerate, onCancel, exportMenu }: VectorizePanelProps) {
  const [advanced, setAdvanced] = useState(invalidField !== null);
  const answer = shownAnswer(item);
  const candidates = catalog.presets.filter((p) => p.auto_candidate).length;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-start gap-3 p-4 pb-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-background ring-1 ring-border">
          <Wand2 className="h-5 w-5" aria-hidden="true" />
        </span>
        <div>
          <h2 className="text-[15px] font-semibold">Vectorize</h2>
          <p className="text-[12px] leading-snug text-muted-foreground">Convert your image to a clean, scalable vector.</p>
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 pb-3">
        <PresetCards
          presets={catalog.presets}
          defaults={catalog.engine.defaults}
          values={item.params}
          active={item.preset}
          auto={item.auto}
          autoRunning={item.job?.key === "auto"}
          onPick={onPick}
        />
        <div className="border-t pt-2">
          <button type="button" aria-expanded={advanced} onClick={() => setAdvanced((v) => !v)} className="mac-ghost h-8 w-full px-1.5 text-foreground">
            <ChevronRight className={`h-4 w-4 transition-transform ${advanced ? "rotate-90" : ""}`} aria-hidden="true" />
            Advanced Options
          </button>
          {advanced && (
            <div className="pt-1">
              <ParamPanel engine={catalog.engine.id} specs={specs} values={item.params} onChange={onParam} invalidField={invalidField} />
            </div>
          )}
        </div>
      </div>
      <div className="space-y-2 border-t p-3">
        {answer && (
          <p className="tabular text-center text-[11px] text-muted-foreground">
            {formatInt(answer.stats.paths)} shapes · {formatInt(answer.stats.nodes)} nodes · {formatBytes(answer.stats.bytes)} · {formatMs(answer.elapsedMs)}
          </p>
        )}
        <TraceButton item={item} candidates={candidates} onGenerate={onGenerate} onCancel={onCancel} />
        {exportMenu}
      </div>
    </div>
  );
}
```

`frontend/src/hooks/useExports.ts`:

```ts
import { useCallback } from "react";
import { toast } from "sonner";

import { baseName, svgToPngBlob } from "@/lib/raster";
import { platform, type Settings } from "@/platform";
import { shownAnswer, type ImageItem, type LibraryState } from "@/state/library";

const fileName = (path: string) => path.split("/").pop() ?? path;

/** Export the selected image's vector (as edited in the layer inspector), copy it, or export every traced image. */
export function useExports(state: LibraryState, item: ImageItem | null, svg: string | undefined, settings: Settings) {
  const exportImage = useCallback(
    async (kind: "svg" | "png", scale: number) => {
      if (!item || !svg) return;
      const stem = baseName(item.image.name);
      try {
        const bytes = kind === "svg" ? new TextEncoder().encode(svg) : new Uint8Array(await (await svgToPngBlob(svg, item.image.width, item.image.height, scale)).arrayBuffer());
        const name = kind === "svg" ? `${stem}.svg` : scale === 1 ? `${stem}.png` : `${stem}@${scale}x.png`;
        const path = await platform.exportFile({ kind, imageId: item.image.id, name, bytes }, settings);
        if (path && platform.kind === "native" && !settings.revealAfterExport) {
          toast.success(`Exported ${fileName(path)}`, { action: { label: "Show in Finder", onClick: () => void platform.reveal(path) } });
        }
      } catch (err) {
        toast.error((err as Error).message);
      }
    },
    [item, svg, settings],
  );

  const exportAll = useCallback(async () => {
    const files = state.items.flatMap((i) => {
      const a = shownAnswer(i);
      return a ? [{ name: `${baseName(i.image.name)}.svg`, svg: a.svg }] : [];
    });
    if (!files.length) return;
    try {
      const written = await platform.exportAll(files, settings);
      if (written && platform.kind === "native") toast.success(`Exported ${written.length} ${written.length === 1 ? "file" : "files"}`);
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [state.items, settings]);

  const copySvg = useCallback(async () => {
    if (!svg) return;
    try {
      await platform.copyText(svg);
      toast.success("Copied SVG");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [svg]);

  return { exportImage, exportAll, copySvg };
}
```

Run: `cd frontend && npx vitest run src/components/PresetCards.test.tsx src/components/TraceButton.test.tsx src/components/ExportMenu.test.tsx src/components/VectorizePanel.test.tsx`
Expected: PASS (12 tests). If Radix's menu does not open on the trigger's `Enter` under jsdom, open it with `fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: "mouse" })` instead; do not change what is asserted.

- [ ] **Step 3: Use them in the app**

In `frontend/src/App.tsx` `Workspace`, replace the interim right panel with:

```tsx
  const exports = useExports(state, item, layers.exportSvg, settings);
  const anyVector = state.items.some((i) => i.shown !== null);
  const invalidField = item?.error?.code === "validation_error" ? (((item.error.detail as { loc?: unknown[] }[] | undefined)?.[0]?.loc?.[1] as string | undefined) ?? null) : null;

  const panel = item ? (
    <VectorizePanel
      item={item}
      catalog={catalog}
      specs={specs}
      invalidField={invalidField}
      onPick={(p) => lib.pickPreset(item.image.id, p)}
      onParam={(name, value) => lib.setParam(item.image.id, name, value)}
      onGenerate={() => lib.generate(item.image.id)}
      onCancel={() => lib.cancel(item.image.id)}
      exportMenu={<ExportMenu canExport={!!layers.exportSvg} anyVector={anyVector} onExport={(k, s) => void exports.exportImage(k, s)} onCopy={() => void exports.copySvg()} onExportAll={() => void exports.exportAll()} />}
    />
  ) : (
    <div className="flex h-full items-center justify-center p-6 text-center text-muted-foreground">Open an image to vectorize it.</div>
  );
```

(drop the `Presets` and `ParamPanel` imports from `App.tsx`; import `VectorizePanel`, `ExportMenu`, `useExports`). Then `git rm frontend/src/components/Presets.tsx frontend/src/components/Presets.test.tsx frontend/src/components/Actions.tsx`.

In `frontend/src/App.test.tsx`, the Auto check in the second test becomes:

```tsx
    expect(screen.getByRole("radio", { name: /^Auto/ })).toHaveAttribute("aria-checked", "true");
```

and add a test that a quick trace updates by itself when a preset is picked:

```tsx
  it("picking a candidate after Auto shows its trace at once", async () => {
    renderApp();
    await screen.findByText("Drop images here");
    drop([png()]);
    await screen.findByRole("option", { name: /logo\.png/ });
    fireEvent.click(screen.getByRole("button", { name: /Generate Vector/ }));
    await screen.findByRole("img", { name: "Vector result" });
    fireEvent.click(screen.getByRole("radio", { name: /^Balanced/ }));
    await waitFor(() => expect(document.querySelector('[data-trace="balanced"]')).not.toBeNull());
    expect(screen.getByText("Up to date")).toBeInTheDocument();
  });
```

- [ ] **Step 4: Run everything**

Run: `cd frontend && npm run test:run && npm run build`
Expected: all pass.

- [ ] **Step 5: Look at it**

In the browser harness: the panel against the concept (header tile, radio cards with icons, the accent ring and filled radio on the checked card, More Styles, Advanced Options, the stats line, the blue Generate Vector button, the Export SVG split button and its menu); trace the Logo sample, pick another candidate (instant), move a slider (Update Vector, or a live re-trace for this quick image); light and dark.

- [ ] **Step 6: Commit**

```bash
git add -A frontend
git commit -m "frontend: the Vectorize panel: preset cards, Advanced Options, Generate/Update/Cancel, Export SVG and its menu

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Menus, shortcuts and context menus in the UI

Every menu item reaches the workspace as one command; the UI tells the menu bar what is possible; in a browser the same commands answer to the same keys; a right click on an image offers what applies to it; the web's own context menu never shows in the app.

**Files:**
- Create: `frontend/src/lib/shortcuts.ts`, `frontend/src/components/ImageMenu.tsx`
- Test: `frontend/src/lib/shortcuts.test.ts`, `frontend/src/App.commands.test.tsx`, `frontend/src/App.native.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/hooks/useExports.ts` (a target image), `frontend/package.json` (`@radix-ui/react-context-menu`)

**Interfaces:**
- Consumes: `MenuCommand`, `MenuState`, `Platform.onMenu/setMenuState/reveal` (Task 6); `ViewerHandle` (Task 9); `useExports` (Task 10); `Sidebar.wrapCard` (Task 8).
- Produces: `type Command = MenuCommand | "open" | "settings"`; `commandForKey(e) -> Command | null`; `ImageMenu({ item, children, onSelect, onGenerate, onExport, onReveal, onRemove })`; in `App.tsx`, `run(command)` and the menu-state effect; `useExports(...).exportImage(kind, scale, target?)`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/lib/shortcuts.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { commandForKey } from "./shortcuts";

const key = (code: string, mods: { shift?: boolean; alt?: boolean; ctrl?: boolean; meta?: boolean } = {}) => ({
  code,
  key: "",
  metaKey: mods.meta ?? true,
  ctrlKey: !!mods.ctrl,
  shiftKey: !!mods.shift,
  altKey: !!mods.alt,
});

describe("commandForKey", () => {
  it("knows the menu's accelerators", () => {
    const table: [ReturnType<typeof key>, string | null][] = [
      [key("KeyO"), "open"],
      [key("KeyE"), "export-svg"],
      [key("KeyE", { shift: true }), "export-png-2"],
      [key("KeyE", { alt: true }), "export-all"],
      [key("KeyC", { shift: true }), "copy-svg"],
      [key("KeyR", { alt: true }), "reveal"],
      [key("Equal"), "zoom-in"],
      [key("Equal", { shift: true }), "zoom-in"],
      [key("Minus"), "zoom-out"],
      [key("Digit0"), "zoom-actual"],
      [key("Digit9"), "zoom-fit"],
      [key("Digit1"), "mode-split"],
      [key("Digit2"), "mode-side"],
      [key("Digit3"), "mode-overlay"],
      [key("Digit4"), "mode-vector"],
      [key("KeyS", { ctrl: true }), "toggle-sidebar"],
      [key("KeyI", { alt: true }), "toggle-inspector"],
      [key("Enter"), "generate"],
      [key("Period"), "cancel"],
      [key("Backspace"), "remove"],
      [key("Comma"), "settings"],
    ];
    for (const [e, want] of table) expect([e.code, e.shiftKey, e.altKey, e.ctrlKey, commandForKey(e)]).toEqual([e.code, e.shiftKey, e.altKey, e.ctrlKey, want]);
  });

  it("ignores what is not one of them", () => {
    expect(commandForKey(key("KeyO", { meta: false }))).toBeNull();
    expect(commandForKey(key("KeyC"))).toBeNull(); // plain ⌘C is the system's Copy
    expect(commandForKey(key("KeyS"))).toBeNull();
    expect(commandForKey(key("KeyQ"))).toBeNull();
  });
});
```

`frontend/src/App.commands.test.tsx` (the browser, where keys stand in for the menu bar):

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import App from "./App";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
});
afterAll(() => server.close());

async function withImage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <App />
    </QueryClientProvider>,
  );
  await screen.findByText("Drop images here");
  const dataTransfer = { types: ["Files"], files: [new File([new Uint8Array([1, 2, 3])], "logo.png", { type: "image/png" })] };
  fireEvent.dragEnter(document, { dataTransfer });
  fireEvent.drop(document, { dataTransfer });
  await screen.findByRole("option", { name: /logo\.png/ });
}

const press = (code: string, mods: Partial<Record<"shiftKey" | "altKey" | "ctrlKey", boolean>> = {}) => fireEvent.keyDown(window, { code, metaKey: true, ...mods });

describe("commands in a browser", () => {
  it("⌘↩ traces, ⌘2 compares side by side, ⌃⌘S hides the sidebar, ⌘⌫ removes the image", async () => {
    await withImage();
    press("Enter");
    expect(await screen.findByRole("img", { name: "Vector result" })).toBeInTheDocument();
    press("Digit2");
    expect(screen.getByRole("tab", { name: "Side by side" })).toHaveAttribute("aria-selected", "true");
    press("KeyS", { ctrlKey: true });
    expect(screen.queryByRole("complementary", { name: "Images" })).toBeNull();
    press("KeyS", { ctrlKey: true });
    press("Backspace");
    await waitFor(() => expect(screen.queryByRole("option", { name: /logo\.png/ })).toBeNull());
    expect(screen.getByText("Drop images here")).toBeInTheDocument();
  });

  it("a right click on an image offers Remove", async () => {
    await withImage();
    fireEvent.contextMenu(screen.getByRole("option", { name: /logo\.png/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByRole("option", { name: /logo\.png/ })).toBeNull());
  });
});
```

`frontend/src/App.native.test.tsx` (the Mac app's wiring, with a fake platform):

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VEXEL, VEXEL_PRESETS } from "@/test/server";

const hooks = vi.hoisted(() => ({ menu: null as null | ((c: string) => void), opens: null as null | ((p: string[]) => void), states: [] as unknown[] }));

vi.mock("@/platform", async () => {
  const types = await vi.importActual<typeof import("@/platform/types")>("@/platform/types");
  const platform = {
    kind: "native",
    health: async () => ({ status: "ok", version: "0.3.0", engines: ["vexel"] }),
    engines: async () => [VEXEL],
    presets: async () => VEXEL_PRESETS,
    pickImages: async () => [],
    openFiles: async () => [],
    openPaths: vi.fn(async (paths: string[]) => paths.map((p) => ({ ok: { id: p, name: p.split("/").pop(), path: p, width: 64, height: 64, format: "PNG", previewUrl: `blob:${p}` } }))),
    closeImage: vi.fn(),
    vectorize: vi.fn(() => new Promise(() => {})),
    exportFile: vi.fn(async () => null),
    exportAll: vi.fn(async () => null),
    copyText: vi.fn(async () => {}),
    reveal: vi.fn(async () => {}),
    loadSettings: async () => types.DEFAULT_SETTINGS,
    saveSettings: async (s: unknown) => s,
    onSettings: () => () => {},
    onMenu: (cb: (c: string) => void) => {
      hooks.menu = cb;
      return () => {};
    },
    onOpenPaths: (cb: (p: string[]) => void) => {
      hooks.opens = cb;
      return () => {};
    },
    onDragState: () => () => {},
    setMenuState: (s: unknown) => hooks.states.push(s),
    openSettingsWindow: () => true,
    windowRole: () => "main",
  };
  return { ...types, platform };
});

const { default: App } = await import("./App");

describe("the Mac app's wiring", () => {
  it("opens what Finder sends, follows the menu, and keeps the menu bar told", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    await waitFor(() => expect(hooks.opens).not.toBeNull());
    expect(hooks.states.at(-1)).toMatchObject({ hasItems: false, hasImage: false, tracing: false, sidebar: true, inspector: true, mode: "split" });

    act(() => hooks.opens!(["/pics/logo.png"]));
    expect(await screen.findByRole("option", { name: /logo\.png/ })).toBeInTheDocument();
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ hasItems: true, hasImage: true, hasPath: true, hasVector: false }));

    act(() => hooks.menu!("generate"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: true }));
    act(() => hooks.menu!("mode-overlay"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ mode: "overlay" }));
    act(() => hooks.menu!("toggle-inspector"));
    expect(screen.queryByRole("complementary", { name: "Vectorize" })).toBeNull();
    act(() => hooks.menu!("cancel"));
    await waitFor(() => expect(hooks.states.at(-1)).toMatchObject({ tracing: false }));
  });
});
```

Run: `cd frontend && npm install @radix-ui/react-context-menu@^2.2.15 && npx vitest run src/lib/shortcuts.test.ts src/App.commands.test.tsx src/App.native.test.tsx`
Expected: FAIL (`./shortcuts` missing; the commands are not wired).

- [ ] **Step 2: Write the key table and the image menu**

`frontend/src/lib/shortcuts.ts`:

```ts
import type { MenuCommand } from "@/platform/types";

/** What the menu bar can ask of the workspace, plus the two the Mac app's Rust side handles itself. */
export type Command = MenuCommand | "open" | "settings";

type Keys = Pick<KeyboardEvent, "code" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey">;

const MODES: Record<string, Command> = { Digit1: "mode-split", Digit2: "mode-side", Digit3: "mode-overlay", Digit4: "mode-vector" };

/**
 * The menu's accelerators, for a browser, where no menu bar owns them. By `code`, not `key`: Option changes the
 * character a key types (⌥E is a dead key), not the key.
 */
export function commandForKey(e: Keys): Command | null {
  if (!e.metaKey) return null;
  const { code, shiftKey: shift, altKey: alt, ctrlKey: ctrl } = e;
  const plain = !shift && !alt && !ctrl;
  if (code in MODES) return plain ? MODES[code] : null;
  switch (code) {
    case "KeyO":
      return plain ? "open" : null;
    case "KeyE":
      return plain ? "export-svg" : shift && !alt && !ctrl ? "export-png-2" : alt && !shift && !ctrl ? "export-all" : null;
    case "KeyC":
      return shift && !alt && !ctrl ? "copy-svg" : null;
    case "KeyR":
      return alt && !shift && !ctrl ? "reveal" : null;
    case "Equal":
      return !alt && !ctrl ? "zoom-in" : null;
    case "Minus":
      return plain ? "zoom-out" : null;
    case "Digit0":
      return plain ? "zoom-actual" : null;
    case "Digit9":
      return plain ? "zoom-fit" : null;
    case "KeyS":
      return ctrl && !alt && !shift ? "toggle-sidebar" : null;
    case "KeyI":
      return alt && !ctrl && !shift ? "toggle-inspector" : null;
    case "Enter":
      return plain ? "generate" : null;
    case "Period":
      return plain ? "cancel" : null;
    case "Backspace":
      return plain ? "remove" : null;
    case "Comma":
      return plain ? "settings" : null;
    default:
      return null;
  }
}

export const isTyping = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
```

`frontend/src/components/ImageMenu.tsx`:

```tsx
import * as ContextMenu from "@radix-ui/react-context-menu";
import type { ReactNode } from "react";

import type { ImageItem } from "@/state/library";

export interface ImageMenuProps {
  item: ImageItem;
  children: ReactNode;
  onSelect: () => void;
  onGenerate: () => void;
  onExport: () => void;
  onReveal: () => void;
  onRemove: () => void;
}

/** A right click on an image card: it is selected, as in Finder, and offers what applies to it. */
export function ImageMenu({ item, children, onSelect, onGenerate, onExport, onReveal, onRemove }: ImageMenuProps) {
  return (
    <ContextMenu.Root onOpenChange={(open) => open && onSelect()}>
      <ContextMenu.Trigger asChild>
        <div>{children}</div>
      </ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content className="mac-menu">
          <ContextMenu.Item className="mac-menu-item" disabled={!!item.job} onSelect={onGenerate}>
            Generate Vector
          </ContextMenu.Item>
          <ContextMenu.Item className="mac-menu-item" disabled={item.shown === null} onSelect={onExport}>
            Export SVG…
          </ContextMenu.Item>
          <ContextMenu.Item className="mac-menu-item" disabled={!item.image.path} onSelect={onReveal}>
            Show in Finder
          </ContextMenu.Item>
          <ContextMenu.Separator className="mac-menu-separator" />
          <ContextMenu.Item className="mac-menu-item" onSelect={onRemove}>
            Remove
          </ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}
```

- [ ] **Step 3: Wire the commands**

In `frontend/src/hooks/useExports.ts`, let `exportImage` take the image to export: `async (kind, scale, target: ImageItem | null = item)`, with the SVG `target === item ? svg : (target ? shownAnswer(target)?.svg : undefined)` (the selected image's is the layer inspector's edited one; any other's is its own trace), and use `target` for the name, size and id.

In `frontend/src/App.tsx` `Workspace`, after `exports`:

```tsx
  const openSettings = useCallback(() => {
    if (!platform.openSettingsWindow()) setSettingsOpen(true); // Task 12 adds the sheet; until then declare `const [settingsOpen, setSettingsOpen] = useState(false)`
  }, []);

  const run = useCallback(
    (cmd: Command) => {
      const id = item?.image.id;
      switch (cmd) {
        case "open":
          return void open(platform.pickImages());
        case "settings":
          return openSettings();
        case "export-svg":
          return void exports.exportImage("svg", 1);
        case "export-png-1":
          return void exports.exportImage("png", 1);
        case "export-png-2":
          return void exports.exportImage("png", 2);
        case "export-png-4":
          return void exports.exportImage("png", 4);
        case "export-all":
          return void exports.exportAll();
        case "copy-svg":
          return void exports.copySvg();
        case "reveal":
          if (item?.image.path) void platform.reveal(item.image.path);
          return;
        case "zoom-in":
          return viewerRef.current?.zoomIn();
        case "zoom-out":
          return viewerRef.current?.zoomOut();
        case "zoom-actual":
          return viewerRef.current?.actualSize();
        case "zoom-fit":
          return viewerRef.current?.fit();
        case "mode-split":
        case "mode-side":
        case "mode-overlay":
        case "mode-vector":
          return setMode(cmd.slice("mode-".length) as ViewMode);
        case "toggle-sidebar":
          return setSidebar((v) => !v);
        case "toggle-inspector":
          return setInspectorPane((v) => !v);
        case "generate":
          if (id) lib.generate(id);
          return;
        case "cancel":
          if (id) lib.cancel(id);
          return;
        case "remove":
          if (id) lib.remove(id);
          return;
        case "clear":
          return lib.clear();
      }
    },
    [item, open, openSettings, exports, setMode, lib],
  );
  // the listeners subscribe once and call whatever `run` is now
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => platform.onMenu((cmd) => runRef.current(cmd)), []);
  useEffect(() => {
    if (platform.kind !== "web") return;
    const onKey = (e: KeyboardEvent) => {
      const cmd = commandForKey(e);
      if (!cmd || (cmd === "remove" && isTyping(e.target))) return;
      e.preventDefault();
      runRef.current(cmd);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // in the app, the web's context menu (Reload, Inspect Element) never shows; text fields keep theirs
  useEffect(() => {
    if (platform.kind !== "native") return;
    const block = (e: MouseEvent) => {
      if (!isTyping(e.target)) e.preventDefault();
    };
    document.addEventListener("contextmenu", block);
    return () => document.removeEventListener("contextmenu", block);
  }, []);

  useEffect(() => {
    platform.setMenuState({
      hasItems: state.items.length > 0 || state.failed.length > 0,
      hasImage: !!item,
      hasVector: !!layers.exportSvg,
      anyVector,
      hasPath: !!item?.image.path,
      tracing: !!item?.job,
      mode,
      sidebar,
      inspector: inspectorPane,
    });
  }, [state.items.length, state.failed.length, item, layers.exportSvg, anyVector, mode, sidebar, inspectorPane]);
```

Pass `onSettings={openSettings}` to `TitleBar`, and `wrapCard` to `Sidebar`:

```tsx
            wrapCard={(it, card) => (
              <ImageMenu
                item={it}
                onSelect={() => lib.select(it.image.id)}
                onGenerate={() => lib.generate(it.image.id)}
                onExport={() => void exports.exportImage("svg", 1, it)}
                onReveal={() => it.image.path && void platform.reveal(it.image.path)}
                onRemove={() => lib.remove(it.image.id)}
              >
                {card}
              </ImageMenu>
            )}
```

(Radix's context menu opens on the `contextmenu` event, which the native-only blocker above lets through: it calls `preventDefault` itself, and the blocker only stops the browser's default.)

- [ ] **Step 4: Run everything**

Run: `cd frontend && npm run test:run && npm run build`
Expected: all pass. Then in the app (`cd apps/desktop && npm run dev`, or a debug bundle): every menu item does what it says; items grey out when they cannot apply (Export with no vector, Show in Finder for a sample, Cancel with nothing running); the View menu's checkmarks follow the mode and the panes; ⌘1–⌘4, ⌘↩, ⌘., ⌘E work from the keyboard; a right click on a card shows the image menu and never "Reload / Inspect Element".

- [ ] **Step 5: Commit**

```bash
git add -A frontend
git commit -m "frontend: menu commands, the menu bar kept told, the same keys in a browser, a context menu per image

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Settings

Settings in a window of their own in the app (⌘,), a sheet in a browser; Appearance, where exports go, Show in Finder after export, Trace new images straight away, Update automatically when tracing is quick. Every change is saved at once and reaches the main window.

**Files:**
- Create: `frontend/src/components/SettingsView.tsx`, `frontend/src/components/SettingsSheet.tsx`
- Test: `frontend/src/components/SettingsView.test.tsx`, `frontend/src/App.settings.test.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/styles.css`, `frontend/package.json` (`@radix-ui/react-dialog`)

**Interfaces:**
- Consumes: `Settings`, `DEFAULT_SETTINGS`, `Platform.loadSettings/saveSettings/onSettings/windowRole/openSettingsWindow` (Task 6); `applyTheme`, `watchSystemTheme`.
- Produces: `SettingsView({ settings, onChange })`; `SettingsSheet({ open, onOpenChange, settings, onChange })`; the settings window's page (`App` renders `SettingsWindow` when `platform.windowRole() === "settings"`).

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/SettingsView.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_SETTINGS } from "@/platform/types";
import { SettingsView } from "./SettingsView";

describe("SettingsView", () => {
  it("shows the settings and hands back each change whole", () => {
    const onChange = vi.fn();
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "System" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, appearance: "dark" });
    fireEvent.click(screen.getByRole("radio", { name: "Next to the original" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, exportTo: "beside" });
    fireEvent.click(screen.getByRole("switch", { name: "Trace new images straight away" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, traceOnOpen: true });
    expect(screen.getByRole("switch", { name: "Update automatically when tracing is quick" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("switch", { name: "Show in Finder after export" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS, revealAfterExport: true });
  });
});
```

`frontend/src/App.settings.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import App from "./App";
import { API_URL } from "@/lib/api";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});
afterAll(() => server.close());

describe("settings in a browser", () => {
  it("open as a sheet from the title bar; Dark applies at once; Trace new images straight away traces on drop", async () => {
    let traces = 0;
    const count = ({ request }: { request: Request }) => {
      if (request.url === `${API_URL}/vectorize`) traces += 1;
    };
    server.events.on("request:start", count);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );
    await screen.findByText("Drop images here");
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    fireEvent.click(screen.getByRole("switch", { name: "Trace new images straight away" }));
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const dataTransfer = { types: ["Files"], files: [new File([new Uint8Array([1, 2, 3])], "logo.png", { type: "image/png" })] };
    fireEvent.dragEnter(document, { dataTransfer });
    fireEvent.drop(document, { dataTransfer });
    expect(await screen.findByRole("img", { name: "Vector result" })).toBeInTheDocument();
    expect(traces).toBe(1);
    expect(JSON.parse(localStorage.getItem("studi0trace.settings")!)).toMatchObject({ appearance: "dark", traceOnOpen: true });
    server.events.removeListener("request:start", count);
  });
});
```

Run: `cd frontend && npm install @radix-ui/react-dialog@^1.1.14 && npx vitest run src/components/SettingsView.test.tsx src/App.settings.test.tsx`
Expected: FAIL.

- [ ] **Step 2: Write the settings**

`frontend/src/components/SettingsView.tsx`:

```tsx
import * as Switch from "@radix-ui/react-switch";
import type { ReactNode } from "react";

import type { Settings } from "@/platform/types";

export interface SettingsViewProps {
  settings: Settings;
  onChange: (next: Settings) => void;
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="px-1 text-[11px] font-semibold text-muted-foreground">{title}</h3>
      <div className="divide-y rounded-xl border bg-card">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-10 items-center justify-between gap-4 px-3 py-2">
      <div className="min-w-0">
        <p className="text-[13px]">{label}</p>
        {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  );
}

function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex shrink-0 rounded-md bg-muted p-0.5">
      {options.map(([v, text]) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className="h-6 rounded-[5px] px-2.5 text-[12px] font-medium text-muted-foreground aria-checked:bg-background aria-checked:text-foreground aria-checked:shadow-sm"
        >
          {text}
        </button>
      ))}
    </div>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <Switch.Root
      aria-label={label}
      checked={checked}
      onCheckedChange={onChange}
      className="relative h-[22px] w-[38px] shrink-0 rounded-full bg-muted-foreground/30 transition-colors data-[state=checked]:bg-[var(--accent-mac)]"
    >
      <Switch.Thumb className="block h-[18px] w-[18px] translate-x-[2px] rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
    </Switch.Root>
  );
}

/** The settings, as grouped rows in the manner of System Settings. */
export function SettingsView({ settings, onChange }: SettingsViewProps) {
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => onChange({ ...settings, [key]: value });
  return (
    <div className="space-y-5 p-5">
      <Group title="Appearance">
        <Row label="Appearance">
          <Choice label="Appearance" value={settings.appearance} options={[["system", "System"], ["light", "Light"], ["dark", "Dark"]]} onChange={(v) => set("appearance", v)} />
        </Row>
      </Group>
      <Group title="Export">
        <Row label="Save exports">
          <Choice label="Save exports" value={settings.exportTo} options={[["ask", "Ask each time"], ["beside", "Next to the original"]]} onChange={(v) => set("exportTo", v)} />
        </Row>
        <Row label="Show in Finder after export">
          <Toggle label="Show in Finder after export" checked={settings.revealAfterExport} onChange={(v) => set("revealAfterExport", v)} />
        </Row>
      </Group>
      <Group title="Tracing">
        <Row label="Trace new images straight away" hint="With Auto, as soon as they are opened">
          <Toggle label="Trace new images straight away" checked={settings.traceOnOpen} onChange={(v) => set("traceOnOpen", v)} />
        </Row>
        <Row label="Update automatically when tracing is quick" hint="Traces again as you change a setting, for images that trace in under 2 seconds">
          <Toggle label="Update automatically when tracing is quick" checked={settings.liveUpdate} onChange={(v) => set("liveUpdate", v)} />
        </Row>
      </Group>
    </div>
  );
}
```

`frontend/src/components/SettingsSheet.tsx`:

```tsx
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import type { Settings } from "@/platform/types";
import { SettingsView } from "./SettingsView";

export interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: Settings;
  onChange: (next: Settings) => void;
}

/** Settings in a browser, where there is no second window to open. */
export function SettingsSheet({ open, onOpenChange, settings, onChange }: SettingsSheetProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30" />
        <Dialog.Content aria-describedby={undefined} className="fixed left-1/2 top-16 z-50 w-[520px] max-w-[calc(100vw-32px)] -translate-x-1/2 overflow-hidden rounded-xl border bg-background shadow-elevated">
          <div className="flex h-11 items-center justify-between border-b px-4">
            <Dialog.Title className="text-[13px] font-semibold">Settings</Dialog.Title>
            <Dialog.Close className="mac-icon" aria-label="Close">
              <X className="h-4 w-4" aria-hidden="true" />
            </Dialog.Close>
          </div>
          <SettingsView settings={settings} onChange={onChange} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
```

- [ ] **Step 3: The settings window and the sheet**

In `frontend/src/App.tsx`:

```tsx
export default function App() {
  return platform.windowRole() === "settings" ? <SettingsWindow /> : <MainWindow />;
}

/** The app's Settings window: the settings, saved as they change, in the window's own appearance. */
function SettingsWindow() {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  useEffect(() => {
    void platform.loadSettings().then(setSettings);
    return platform.onSettings(setSettings);
  }, []);
  useEffect(() => {
    applyTheme(settings.appearance);
    return watchSystemTheme(() => settings.appearance);
  }, [settings.appearance]);
  const change = (next: Settings) => {
    setSettings(next);
    void platform.saveSettings(next);
  };
  return (
    <div className="min-h-dvh bg-background">
      <SettingsView settings={settings} onChange={change} />
    </div>
  );
}
```

(the previous `App` body becomes `function MainWindow()`). In `MainWindow`, pass `onSettingsChange={(next) => { setSettings(next); void platform.saveSettings(next); }}` down to `Workspace`; in `Workspace` add `const [settingsOpen, setSettingsOpen] = useState(false);` (replacing Task 11's placeholder) and render, beside the shell:

```tsx
      <SettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} settings={settings} onChange={onSettingsChange} />
```

In `frontend/src/main.tsx`, after the `native` class: `if (platform.kind === "native" && platform.windowRole() === "settings") document.documentElement.classList.add("settings-window");` and in `styles.css` `@layer base`:

```css
  /* the Settings window is not transparent: paint the page */
  html.native.settings-window,
  html.native.settings-window body {
    background: hsl(var(--background));
  }
```

- [ ] **Step 4: Run everything**

Run: `cd frontend && npm run test:run && npm run build`
Expected: all pass. In the app: ⌘, opens the Settings window (a second ⌘, brings it forward); choosing Dark turns both windows dark at once; Trace new images straight away makes a dropped image trace by itself; quitting and reopening keeps the settings.

- [ ] **Step 5: Commit**

```bash
git add -A frontend
git commit -m "frontend: Settings, a window of their own in the app and a sheet in a browser, saved as they change

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Finder, the bundle, the smoke test and the docs

Studi0Trace appears in Finder's Open With for images (never taking over as their default app), a release build makes `Studi0Trace.app` and a `.dmg`, a smoke script proves the built app opens a file the way Finder hands it one and runs a trace in a worker, and the docs say how to run and build the app.

**Files:**
- Modify: `apps/desktop/src-tauri/tauri.conf.json` (`bundle.fileAssociations`), `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-02-studi0trace-mac-app-design.md` (only where the build taught something new)
- Create: `apps/desktop/scripts/smoke.sh`

**Interfaces:**
- Consumes: everything above. `RunEvent::Opened` → `open-paths` (Task 5) → the UI's `openPaths` (Tasks 6, 8) → `note_recent` (Task 5) writes `recent` into `~/Library/Application Support/com.studi0.trace/settings.json`; `traceOnOpen` (Task 12) traces the opened image in a `--trace-worker` child (Task 3).
- Produces: `npm run build` → `target/release/bundle/macos/Studi0Trace.app` and `target/release/bundle/dmg/Studi0Trace_0.3.0_aarch64.dmg`; `npm run smoke`.

- [ ] **Step 1: Register the image types**

In `apps/desktop/src-tauri/tauri.conf.json`, add to `bundle` (`rank: Alternate` lists the app under Open With without making it any type's default; `public.heic` is given explicitly because Tauri would infer `public.heif-standard-image`):

```json
    "fileAssociations": [
      { "ext": ["png"], "name": "PNG image", "role": "Viewer", "rank": "Alternate", "contentTypes": ["public.png"] },
      { "ext": ["jpg", "jpeg"], "name": "JPEG image", "role": "Viewer", "rank": "Alternate", "contentTypes": ["public.jpeg"] },
      { "ext": ["gif"], "name": "GIF image", "role": "Viewer", "rank": "Alternate", "contentTypes": ["com.compuserve.gif"] },
      { "ext": ["webp"], "name": "WebP image", "role": "Viewer", "rank": "Alternate", "contentTypes": ["org.webmproject.webp"] },
      { "ext": ["bmp"], "name": "BMP image", "role": "Viewer", "rank": "Alternate", "contentTypes": ["com.microsoft.bmp"] },
      { "ext": ["heic", "heif"], "name": "HEIC image", "role": "Viewer", "rank": "Alternate", "contentTypes": ["public.heic", "public.heif"] },
      { "ext": ["tif", "tiff"], "name": "TIFF image", "role": "Viewer", "rank": "Alternate", "contentTypes": ["public.tiff"] }
    ]
```

- [ ] **Step 2: Write the smoke script**

`apps/desktop/scripts/smoke.sh`:

```bash
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
```

`chmod +x apps/desktop/scripts/smoke.sh`, and add `"smoke": "bash scripts/smoke.sh"` to `apps/desktop/package.json`'s scripts.

(If the trace finishes between two polls the worker can be missed: logo.png traces in about a second and a poll is every 0.2 s, so this does not happen in practice; if it does, point `SAMPLE` at a larger corpus image, e.g. `backend/bench/corpus/real/logo/vexel-wordmark-512.png`.)

- [ ] **Step 3: Build and run it**

```bash
cd apps/desktop && npm run build
ls ../../target/release/bundle/macos/Studi0Trace.app ../../target/release/bundle/dmg/*.dmg
SKIP_BUILD=1 npm run smoke
```

Expected: the bundle and the dmg exist; the smoke prints `yes` twice and exits 0. Also check: `codesign -dv ../../target/release/bundle/macos/Studi0Trace.app 2>&1 | grep -i signature` says `adhoc` (the linker's ad-hoc signature, no identity); `plutil -p ../../target/release/bundle/macos/Studi0Trace.app/Contents/Info.plist | grep -A3 CFBundleDocumentTypes` lists the image types; `du -sh` of the app is in the tens of megabytes.

- [ ] **Step 4: The docs**

`README.md`: a new first section, **Studi0Trace for Mac**, before the server's reference: what the app is (a Mac app; images stay on the Mac; free and MIT); run it from source (`cd apps/desktop && npm install && npm run dev`, which starts the frontend's Vite server itself); build it (`npm run build` → the `.app` and `.dmg` paths above); open an unsigned build ("The app is not signed. The first time, macOS 15 and later refuse to open it: open System Settings ▸ Privacy & Security and click Open Anyway under the message about Studi0Trace, or run `xattr -dr com.apple.quarantine /Applications/Studi0Trace.app`."); what it opens (PNG, JPEG, GIF, WebP, BMP, HEIC, TIFF, up to 2048 px a side, and Downscale for larger ones); the keyboard shortcuts table (⌘O, ⌘↩, ⌘., ⌘E, ⇧⌘E, ⌥⌘E, ⇧⌘C, ⌘1–⌘4, ⌘+, ⌘-, ⌘0, ⌘9, ⌃⌘S, ⌥⌘I, ⌘⌫, ⌘,). Say that the FastAPI server and the browser build remain for development until plan 4 retires them.

`CLAUDE.md`, under Layout, a new entry after `crates/studi0trace-core/`:

```markdown
- `apps/desktop/` — Studi0Trace for Mac (Tauri 2; plan 2, `docs/superpowers/plans/2026-10-02-studi0trace-mac-app.md`).
  `src-tauri` is a workspace member holding `Core` for describing, presets and intake; every trace runs in a
  child process, the app's own binary with `--trace-worker`, one at a time (`queue.rs`), killed to cancel.
  HEIC/HEIF/TIFF and Downscale go through `/usr/bin/sips`. The webview never names a path to write: Rust shows
  the panels and writes (`export.rs`). `npm run dev` / `npm run build` / `npm run smoke` from `apps/desktop`;
  `cargo test -p studi0trace-desktop --release` includes the worker tests on the real binary. The crate embeds
  `frontend/dist` when it compiles, so `cd frontend && npm run build` comes before any cargo build of the workspace.
```

and under Conventions:

```markdown
- The frontend talks to its host only through `src/platform` (`native`: Tauri commands and events; `web`: the
  Python server, kept as a development harness). Images live in `src/state/library.ts`, a store with no React
  in it; components read it with `useLibrary`. Never call `invoke` or `fetch` from a component.
```

- [ ] **Step 5: Verify everything once more**

```bash
cargo test --workspace --release
cargo clippy -p studi0trace-desktop --release --all-targets
cd backend && .venv/bin/python -m pytest -o addopts="" -q tests/test_vexel_lock_matches_workspace.py && cd ..
cd frontend && npm run test:run && npm run build && cd ..
cd apps/desktop && SKIP_BUILD=1 npm run smoke && cd ../..
```

Expected: every command passes. Record the counts (Rust tests, frontend tests) for the final report.

- [ ] **Step 6: Commit**

```bash
git add -A apps/desktop README.md CLAUDE.md docs/superpowers/specs/2026-10-02-studi0trace-mac-app-design.md
git commit -m "desktop: Open With for images, the release bundle and dmg, a smoke test of the built app, the docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Execution notes

- **Order.** Tasks 1–5 (Rust) and 6–7 (frontend) do not depend on each other and may be interleaved; 8 needs 6 and 7; 9–12 need 8, in order; 13 needs all. Each task is reviewed before the next starts.
- **Autonomy.** Tim asked for this to run without check-ins. Decide what the plan leaves open by its spec, write the decision in the task's report, and go on. Stop and ask only for something that cannot be undone or that changes what ships (a new dependency with C code in the core, signing, anything that touches `main`).
- **Looking.** Implementers verify their UI in the browser harness (`.claude/launch.json`); where a subagent has no browser tools, the controller does it after the review. This machine's Claude app cannot screenshot windows (no Screen Recording permission), so the native window cannot be seen by an agent; the smoke test, `osascript` on the menu bar (if System Events may be scripted) and the logs stand in for it, and what only a person can judge goes in the final report.
- **For Tim's eyes, at the end** (list it in the report): the traffic lights' place in the title bar (`trafficLightPosition` is a guess at 18, 20); the sidebar's vibrancy in light and dark; that the accent follows System Settings ▸ Appearance ▸ Accent colour; that no second Dock icon appears while a trace runs; the menus; the feel of pinch and scroll; the icon in the Dock.
- **Never** merge or push to `main` without Tim's word (Render deploys from it); never sign; never `--all-features`; never `rm -rf`; never a bare `git stash`.
