# Studi0Trace as a local app: design and roadmap

2026-09-24. Studi0Trace becomes a free, open-source (MIT) Mac app, with the web version kept
and held to the same output. Nothing Python ships. Potrace and VTracer leave the product.
The UI is refined afterwards, by hand, once the app runs locally.

## Decisions

| question | decision | why |
|---|---|---|
| Licence | MIT (already the repo's) | free and open source |
| Python | none in anything that ships; the Python reference engine, the bench and `tools/diffcheck.py` stay as developer tools | the reference is what every engine change is checked against, and what makes the published benchmark reproducible |
| Engines in the product | Vexel only | Potrace and VTracer stay only as optional bench adapters, so the published comparison can be re-run |
| Mac shell | Tauri 2 | the engine is already Rust and links in-process; the existing React UI runs unchanged in the webview; a ~20–30 MB app instead of a 300 MB Python bundle |
| Web version | the same Rust core compiled to WebAssembly, running in a Web Worker; a static site with no server | parity by construction (one engine, two targets), no hosting to run, images never leave the browser |
| Distribution | signed with the maintainer's Developer ID and notarized in GitHub Actions; published on GitHub Releases; the app updates itself through Tauri's updater | since macOS 15 an unsigned app cannot be opened with right-click → Open; open source does not preclude signing, and anyone can still build unsigned from source |

## Architecture

```
Cargo.toml (workspace)
├─ backend/vexel-rs/          the engine (unchanged location; its pyo3 module stays for dev tools)
├─ crates/studi0trace-core/   everything the Python server did around the engine:
│                             image intake, parameters and their schema, presets, SVG finishing,
│                             the quality scorecard, Auto, and one facade (`Core`) the shells call
├─ crates/studi0trace-wasm/   wasm-bindgen wrapper of `Core` for the browser        (plan 3)
└─ apps/desktop/src-tauri/    Tauri 2 app: `Core` behind commands                  (plan 2)

frontend/                     one React app, two transports behind one interface:
                              `native` (Tauri invoke) and `wasm` (a Web Worker)
backend/ (Python)             developer tooling only: reference engine, bench, diffcheck
```

The frontend already talks to the backend through five calls in `frontend/src/lib/api.ts`
(`getHealth`, `getEngines`, `getPresets`, `uploadImage`, `vectorize`). `Core` returns the same
JSON shapes those calls return today, so the UI changes only at the transport.

## Parity

- **Python reference ↔ Rust core.** The core's new code (intake, schema, presets, scorecard,
  Auto) is ported from the Python and held to it with golden fixtures exported by
  `backend/tools/export_core_fixtures.py`, and a `scorecard` stage in `tools/diffcheck.py`.
- **Desktop ↔ web.** Both run `Core`. The one known source of drift is transcendental maths
  (`sin`, `exp`, `powf`, `atan2`): native builds call the platform's libm, WebAssembly calls
  Rust's. Plan 3 measures it (every corpus item traced by both, SVG bytes compared); only if
  they differ are those calls routed through the `libm` crate in both targets.
- **Image decoding** moves from Pillow to the `image` crate. PNG decodes identically; JPEG may
  differ by a level or two per pixel. The app and the web share the decoder, so they agree
  with each other; the bench keeps decoding with Pillow and says so.

## Plans

1. **Core crate** (`docs/superpowers/plans/2026-09-24-studi0trace-core-crate.md`): the workspace,
   `studi0trace-core` with every piece above, golden parity with the Python. Deliverable: a
   `cargo run --example trace` that traces the wordmark with Auto and prints the pick, byte-identical
   SVG to the Python API's.
2. **Desktop app**: Tauri 2 in `apps/desktop`; commands `describe`, `presets`, `open_image`
   (path or bytes), `trace`, `auto`, `save_svg`; the frontend's `native` transport; open and save
   dialogs, drag and drop from Finder, export next to the original, recent files, settings kept
   between launches; `cargo tauri dev` runs it. Deliverable: the local app, feature-equal with
   the web app, ready for UI refinement.
3. **Web on WebAssembly**: `crates/studi0trace-wasm`; rayon made optional in `vexel-rs` (a
   `parallel` feature, on for native, off for wasm); a Web Worker hosting `Core`; the `wasm`
   transport; the corpus traced natively and in wasm with SVG bytes compared; a static deploy
   (`render.yaml` reduced to the static site). Deliverable: the web version with no server.
4. **Release and retirement**: GitHub Actions building a universal (arm64 + x86_64) app,
   signing (Developer ID Application), notarizing (`notarytool`), stapling, publishing to
   GitHub Releases with an updater manifest; third-party licence notices (`cargo about`);
   the FastAPI app, `Dockerfile`, the Potrace/VTracer runtime engines and the Python backend
   deploy removed; README and CONTRIBUTING rewritten for the app.

UI refinement follows plan 2 and is led by hand; the web version inherits it because the
frontend is shared.

## Risks

- **Scorecard port size.** `imaging/quality.py` is 1,141 lines of geometry. It is ported
  function by function against golden fixtures; nothing in it is redesigned during the port.
- **WebAssembly speed and memory.** Tracing is single-threaded in wasm unless the site is
  served cross-origin isolated (COOP/COEP) with `wasm-bindgen-rayon`. Expect about twice the
  native time; Auto's four candidates may need a progress indicator.
- **Renderer version.** The scorecard's numbers depend on the renderer; the Rust core pins the
  `resvg` release that `resvg-py` 0.5.0 bundles, so the Python and Rust scorecards render alike.

## Open questions (none block plan 1)

- App name and bundle identifier (`com.studi0.trace`?), minimum macOS version (13?), and
  whether to ship Intel as well as Apple silicon (universal binary assumed).
- Where the static web version is hosted (Render static site, GitHub Pages or Cloudflare Pages).
