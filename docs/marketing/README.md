# Marketing assets

Everything here is made from the real app: the screenshots are the built `Studi0Trace.app` at 1280 × 800 on a
Retina display, tracing `backend/bench/heldout/fluent-color/cherries-512.png` (Microsoft Fluent Emoji, MIT).

| file | what | where it is used |
|---|---|---|
| `hero-dark.png`, `hero-light.png` | the whole window after a trace, split view, both appearances | the README's hero and its "Made for the Mac" tile |
| `auto.png` | the Auto card with the styles open | README |
| `inspect.png` | the Inspect tab with anchor points on | README |
| `export.png` | the export menu | README |
| `app-icon.png` | the app icon at 256 px | README |
| `social-preview.png` | 1280 × 640 card: icon, wordmark, tagline, the window | GitHub's repository social preview; link previews on X, LinkedIn, Mastodon, Slack |

**Set the GitHub social preview:** repository ▸ Settings ▸ General ▸ Social preview ▸ Upload an image ▸
`social-preview.png`. GitHub shows it on every link to the repo.

**Retake the screenshots** after a UI change: build the app (`cd apps/desktop && npm run build`), open the
cherries file with it, set the window to 1280 × 800, trace with ⌘↩, and capture the window at 2×. The
crops are the window at 2× cut to these regions (in window points): `auto` (762, 40)–(1272, 450),
`inspect` (8, 40)–(600, 700), `export` (762, 410)–(1272, 790), each with 20 px rounded corners; the hero is
the whole window with the window's own 24 px corners.

## Posts, ready to paste

**X / Mastodon / Bluesky**

> Studi0Trace: a free, open-source Mac app that turns a PNG of a logo or illustration into a clean SVG.
> Real gradients and drop shadows, straight edges that stay straight, no hairlines between shapes.
> Everything runs on your Mac.
> https://github.com/timothynice/tracer

**LinkedIn**

> I built Studi0Trace, a Mac app that traces images into vectors you'd actually ship.
>
> Most tracers flatten an image into a few colours and outline the bands. Studi0Trace reads it the way a
> designer drew it: a gradient comes back as one shape with one gradient, a drop shadow as the SVG filter that
> made it, a circle as a circle, and two shapes that touch share one edge, so there is never a hairline between
> them. On a bench of 104 images it is the most faithful on 102, with a third of the paths.
>
> It's free, MIT licensed, and nothing leaves your Mac. Download it or read how the engine works:
> https://github.com/timothynice/tracer

**Short**

> Turn images into clean vectors. Studi0Trace for Mac, free and open source.
> https://github.com/timothynice/tracer
