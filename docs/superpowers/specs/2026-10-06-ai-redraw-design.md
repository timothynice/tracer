# AI redraw — design

Status: approved by Tim 2026-10-06 (flow section approved in chat; "build it autonomously so I test it in the
final app"). Branch `claude/ai-redraw`.

## Why

A small, blurry or pixel-doubled raster is the hardest input Vexel has. Tim found that asking OpenAI's image model
to redraw such a logo "full size" gave a clean image that traced very well. Measured on the wave-lockup asset the
redraw is also a *different drawing*: after aligning the artwork's bounding boxes, edge F1 against the original is
0.59 (our own trace of the original: 0.997), mean CIEDE2000 3.8, the wordmark's proportions and letter spacing
moved and the small caps' blue became more saturated. So the redraw is useful and unsafe at once: it must be opt-in,
uploads only with consent and the user's own key, and always shows how far it moved the image before anything is
traced from it. It does not replace engine work on hard inputs.

## Flow (approved)

1. **Offer.** "Redraw with AI…" is always available for an open image (image menu, inspector, app menu). The
   inspector also shows a quiet hint on an image that *looks rough* — longest side under `ROUGH_SIDE` 600 px, or an
   exact 2× nearest-neighbour upscale on both axes (every complete row pair and column pair byte-identical at some
   phase, the native image not itself doubled — the rule held on all 224 corpus and held-out items in the
   wave-lockup work): "This image is small — an AI redraw may trace cleaner." Hint style: `.mac-dot` plus a muted
   tint, never a one-sided border. Nothing runs without a click.
2. **First use.** A consent sheet: the image is uploaded to OpenAI with *your* API key and billed to your account;
   the redraw may change shapes, spacing and colours, and you check it before it is used. The user pastes a key,
   stored in the macOS Keychain (never `settings.json`, never the webview's storage). The sheet does not return
   while a key is stored. Settings gains an **AI redraw** group: key status (Stored / Not set) with Replace and
   Remove, Model, Quality, and "Suggest for rough images" (default on).
3. **Redraw.** The source is padded with its own border colour to OpenAI's aspect range, sent to the image edit
   endpoint with a fixed prompt, the reply cropped back to the original framing and fitted within the app's
   2048 px side. Progress shows "Redrawing with AI…"; Cancel abandons the request and discards any reply that still arrives. 10–60 s is typical.
4. **Drift check, always.** Before anything changes: original against redraw in the Viewer (overlay, split, side by
   side, raster against raster), the two drift numbers and a verdict chip, with **Use redraw**, **Try again**,
   **Discard**.
5. **After "Use redraw".** The image's source becomes the redraw: traces run on it (and immediately if *Trace on
   open* is set). The original stays attached: **Show original** (view) and **Revert to original** (source swaps
   back, traces of the original are kept) in the image menu. An image whose source is a redraw carries an "AI
   redraw" chip in the inspector.
6. **Copy.** README: "Your images stay on your Mac: nothing is uploaded unless you choose AI redraw, which sends that
   one image to OpenAI with your own API key." The same sentence sits in the consent sheet.

## The request

- Endpoint `POST https://api.openai.com/v1/images/edits`, multipart: `model`, `image` (the padded PNG), `prompt`,
  `size`, `quality`, `output_format=png`, `background=opaque` (the redraw is compared and traced on an opaque
  ground; a transparent source is composited on white for the request and its alpha is not restored — out of scope),
  `n=1`. Bearer auth with the stored key. Timeout 120 s.
- Models offered: `gpt-image-2` (default), `gpt-image-1.5`. `gpt-image-1.5` additionally gets `input_fidelity=high`
  (the parameter exists only for the 1.x models). Quality: `medium` (default) or `high`.
- **Aspect.** `gpt-image-2` sizes are `WxH` with both multiples of 16, aspect 1:3 to 3:1, at most 3840×2160, at
  least 655 360 px in total. The source (w × h) is padded symmetrically to the nearest aspect inside [1/3, 3] with
  its border colour (the median of its outermost one-pixel frame). The request size has the padded aspect, long side
  `REQUEST_SIDE` 2048, both sides rounded to multiples of 16, raised if needed to the pixel minimum. For
  `gpt-image-1.5`, whose sizes are fixed, the closest of `1024x1024`, `1536x1024`, `1024x1536` by aspect is used and
  the padding is to that aspect.
- **Prompt** (one constant, both models): "Redraw this exact image at high resolution as clean flat artwork. Keep
  every shape, letter, proportion, spacing, position and colour exactly as in the input; do not add, remove, restyle
  or re-letter anything. Remove blur, compression noise, halos and pixelation. Keep the background a plain flat
  colour and keep the same framing and margins."
- **Back to the original framing.** The reply is scaled by the factor that maps the request size back to the padded
  size, the padding is cropped away, and the result is resized (Lanczos, `core::resample`) so that it has the
  original's aspect at the largest size within 2048 px and at least the original's size. It re-enters through
  `intake::open_bytes` like any pasted image (validated, its own core id).

## Drift

Computed in Rust from the core's existing measures, no new metric to port: the redraw is resized to the original's
size, both composited on white, and `scorecard::Reference::new(original).fidelity(redraw)` gives `edge_f1` (Canny
edges matched within 2 px) and `delta_e_mean`. Verdict = the worse of the two scales:

| verdict | edge F1 | mean ΔE |
|---|---|---|
| Close | ≥ 0.95 | ≤ 2.5 |
| Noticeable | ≥ 0.80 | ≤ 5.0 |
| Large | below | above |

Calibrated on the wave-lockup asset: the original against itself resized down 2× and back up scores 1.00 / 0.9
(Close); our trace of it 0.997 / 1.5 (Close); Tim's ChatGPT redraw 0.59 / 3.8 (Large). The numbers are shown as
"Edges matched: 59 %" and "Colour shift: ΔE 3.8". The drift is computed off the main thread (the reference costs
~34 B/px).

## Architecture

Everything that touches the network or the key is Rust in the Tauri shell; the webview's CSP stays without
`connect-src`, the capabilities file does not change, the trace worker stays offline, and `studi0trace-core` stays
wasm-clean (no network, no filesystem).

- `apps/desktop/src-tauri/src/redraw/` — one responsibility per file:
  - `geometry.rs` — pure: border colour, pad-to-aspect, request size, crop-back rectangle, final size. Unit-tested.
  - `openai.rs` — builds the multipart request and parses the reply (`data[0].b64_json`, error bodies
    `{"error":{"message","code","type"}}`); base URL from `STUDI0TRACE_OPENAI_BASE` when set (tests), else
    `https://api.openai.com/v1`. HTTP through `reqwest` (already in the tree via the updater; same TLS stack,
    no new one) with `multipart`.
  - `drift.rs` — resize, composite, `Reference::fidelity`, verdict.
  - `rough.rs` — the *looks rough* rule.
  - `mod.rs` — `Redraws`, the in-flight jobs (one per image; a new request for an image cancels the old), and the
    pending results awaiting a decision.
- `keychain.rs` — `security-framework` generic password, service `com.studi0.trace.openai`, account `api-key`:
  `has_key`, `set_key`, `delete_key`, `read_key` (Rust-only; the key never crosses into the webview).
- `store.rs` — an image's entry gains `original: Option<id>`; accept/revert swap which bytes the image's id traces.
- `settings.rs` / `frontend/src/platform/types.ts` — `redrawModel`, `redrawQuality`, `suggestRedraw` (no key).
- Commands (`commands.rs`): `redraw_key_status`, `set_redraw_key`, `delete_redraw_key`, `redraw_image(id)` →
  emits `redraw-phase` (`uploading` | `drawing` | `checking` | `done` | `failed`) and answers
  `{ redraw: OpenedImage, drift: { edgeF1, deltaE, verdict } }`, `cancel_redraw(id)`, `accept_redraw(id)`,
  `discard_redraw(id)`, `revert_redraw(id)`, `image_roughness(id)` → `{ rough: bool, reason }`.
- Frontend: `Platform` gains the matching methods (the `web` platform answers "not available" for all of them);
  `state/library.ts` `ImageItem` gains `redraw?: { pending?: { image, drift }, original?: OpenedImage, active: boolean, phase? }`
  and `rough?`; components: `RedrawHint`, `RedrawConsentSheet`, `DriftCheck` (wraps `Viewer` in a raster-vs-raster
  mode), the Settings group, menu items (`MenuCommand` + `menu.rs`). No component calls `invoke`.

## Errors (stable codes, shown in plain words, never the key)

`no_key` (opens the consent sheet), `invalid_key` (401), `quota` (429 / insufficient_quota: "your OpenAI account is
out of credit or rate limited"), `refused` (content policy), `timeout`, `offline` (connect error), `bad_reply`
(no image / undecodable), `too_large` (source over the 50 MB request limit; cannot happen under the 20 MB intake
cap, kept for safety), `cancelled` (silent). Each leaves the image exactly as it was.

## Testing

- Rust unit tests: geometry (aspects 1:5, 3.92:1, 1:1, 1:3 exact, tiny and 2048 sources; multiples of 16; the
  pixel minimum), the rough rule (doubled at both phases, doubled twice, crisp 3-colour art, 599/600 px), drift
  verdict boundaries, reply/error parsing.
- Rust integration test against a local mock server (`std::net::TcpListener` on 127.0.0.1, no new crate) through
  `STUDI0TRACE_OPENAI_BASE`: success, 401, 429, garbage body, slow reply cancelled; asserts the multipart fields
  sent and that the key appears only in the Authorization header.
- Keychain: a test behind `STUDI0TRACE_TEST_KEYCHAIN=1` (set/has/read/delete under a test service name), skipped by
  default (CI has no login keychain).
- Frontend (vitest, mocked `Platform`): hint shown only for rough images and when the setting is on; consent sheet
  on `no_key`; the drift view's verdict chip and three buttons; accept swaps the source and re-traces; revert;
  errors rendered by code.
- No automated test calls OpenAI. A live check is an `#[ignore]`d test, `live_redraw`, run by hand with
  `OPENAI_API_KEY=… cargo test -p studi0trace-desktop --release -- --ignored live_redraw`: it redraws a core
  fixture through `redraw::openai` + `geometry` + `drift` and prints the size, drift and verdict.

## Out of scope

Restoring a transparent source's alpha; batch redraw; local (on-device) super-resolution; other providers; keeping redraws across launches (the image store is in memory, as it is for every open image); any change to the trace engine.
