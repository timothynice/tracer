# Vexel shadow, glow and inner-shadow reconstruction

Status: accepted, 2026-09-18.

## Problem

Vexel has no model for a blur. A soft drop shadow is a smooth falloff, so the
region partition slices it into a handful of bands and fits each with a
gradient. The band boundaries are iso-contours of the blur, which trace as
irregular lumpy outlines — visibly "chunky" against the smooth original.

`frontend/public/samples/shadow.png`, traced with defaults, spends four of its
seven paths on the shadow (`g4`–`g7`, 7 849 characters of path data between
them) and still reads wrong.

That sample is `shadow_card` from `bench/synth.py`: a rounded rect drawn with

```
feGaussianBlur(SourceAlpha, σ=16) → feOffset(0, 24) → feFuncA slope 0.45
```

merged under the source graphic. The information the bands are approximating
is four numbers and a colour.

## Approach

Recognise the effect rather than approximate its appearance. Where a group of
low-contrast regions is explained as a blurred, offset, scaled copy of a
neighbouring shape's alpha, drop those regions and emit an SVG `<filter>` on
the shape that casts them.

For art authored this way — which is most shadowed vector art, whatever tool
drew it — this inverts the generative model exactly. Four lumpy paths become
zero extra paths and a filter.

Rejected alternatives:

- **More gradient stops.** A drop shadow's falloff is a distance field around
  the caster's outline, not a linear or radial ramp. No number of stops in a
  `linearGradient`/`radialGradient` expresses it; the banding is structural.
- **Stacked contours.** Many nested offset shapes at low opacity. Bloats the
  output, still bands, and is unusable for editing.

## Scope

Three effects, one shared model:

| effect | model |
|---|---|
| drop shadow | `opacity · G_σ(shift_{dx,dy}(A))`, painted under the caster |
| glow | the same with `dx = dy = 0` and a light colour |
| inner shadow | `opacity · G_σ(shift_{dx,dy}(1 − A))` clipped to `A`, painted over |

`A` is the caster's anti-aliased alpha coverage. General blur ("this artwork is
blurry") is out of scope: it has no caster to anchor it and no bound.

## Design

### `engines/vexel/shadows.py`

One analysis entry point, following `overlaps.py`: analyse, return a plan, let
the orchestrator apply it.

```python
@dataclass(frozen=True)
class Shadow:
    caster: int          # label of the shape that casts it
    dx: float
    dy: float
    sigma: float
    colour: np.ndarray   # rgb 0-255
    opacity: float       # 0..1
    inset: bool
    rms: float           # fit residual in alpha units

@dataclass
class ShadowPlan:
    shadows: dict[int, Shadow]   # caster label -> its shadow
    absorbed: set[int]           # regions the shadows explain; no longer painted

def detect_shadows(labels, fills, visible, enc, prep, *, tol, curve_params) -> ShadowPlan
def shadow_filter_svg(shadow: Shadow, fid: str, precision: int) -> str
```

### Detection

1. **Candidates.** Regions whose fitted colour is a low-contrast variant of an
   adjacent larger region (the *backdrop*), and which are not themselves opaque
   ink. Contiguous candidates sharing a backdrop form one group.
2. **Caster.** Opaque shapes adjacent to the group, or enclosing it for the
   inset case. The largest wins; ties go to the one whose alpha centroid is
   nearest the group's.
3. **Alpha recovery.** Over the group, `observed = α·C + (1−α)·B(x, y)` with
   `B` evaluated from the **backdrop's fitted fill**, so a shadow over a
   gradient recovers correctly. The shadow colour direction `C − B` comes from
   a least-squares fit over the group; `α` is the projection onto it,
   normalised so `max α ≤ 1`.
4. **Parameter fit.** Minimise `‖opacity · G_σ(shift_{dx,dy}(A)) − α_obs‖²`.
   `σ` over a coarse octave grid, `(dx, dy)` seeded from the centroid offset
   between `α_obs` and `A`, then Nelder-Mead; `opacity` is closed-form by least
   squares at each step. The blur runs on a downsampled field when the image is
   large, since `σ` scales with it.
5. **Acceptance.** Take the fit only when its residual beats the banded
   alternative it would replace. Everything else falls through to the current
   code path untouched, so the worst case is exactly today's output.

### Emission

The primitive chains, all three verified to render in resvg (the bench's
rasteriser) before this was designed:

- black shadow: `feGaussianBlur(SourceAlpha) → feOffset → feComponentTransfer`
  → `feMerge` with `SourceGraphic`
- coloured: `feFlood` + `feComposite operator="in"` in place of the transfer
- inset: `feComposite operator="out"` against `SourceAlpha`, clipped back with
  `operator="in"`, merged *over* `SourceGraphic`

`color-interpolation-filters="sRGB"` on every filter — the default of
`linearRGB` changes the appearance.

### Integration

In `trace_rgba`, after `refine_merge` and before stroke grouping. Absorbed
regions join `skip`; the caster's element gains `filter="url(#fN)"`; the filter
goes in `defs`.

New parameter:

```python
shadows: bool = Field(True, description="Rebuild drop shadows, glows and inner
    shadows as SVG filters instead of banded paths",
    json_schema_extra={"ui": {"control": "toggle", "group": "Effects"}})
```

Default on. Off restores the current behaviour, for consumers that ignore
filters (cutting software, some older importers). The schema-driven UI picks up
the new **Effects** group with no frontend change.

## Verification

Inner shadows are not in the corpus, so `synth.py` gains `shadow_inset_*`
generators and the corpus is regenerated before any claim is made about them.

- Unit: alpha recovery against a known backdrop; round-trip (synthesise a known
  `σ, dx, dy, opacity`, fit it, assert recovery within tolerance); rejection on
  art with no shadow; the three emitted filter forms parse and raster.
- Engine: `shadow.png` emits a `<filter>` and at most three paths.
- Bench against `baselines/vexel.json`. The shadow class should move
  substantially; every other class must stay flat. **If any class regresses,
  the acceptance threshold tightens — the baseline does not move.**

The risk this guards against is a false positive: soft shading in an
illustration read as a shadow and flattened into a filter. The `flat` and
`gradient` bench classes are the detector for that.
