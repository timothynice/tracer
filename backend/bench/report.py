"""Static HTML report for a results.json, styled with the Studi0 tokens."""
from __future__ import annotations

import html
import json
from pathlib import Path

from bench.config import CLASSES

SUMMARY_COLS = [
    ("score", "Score", "{:.3f}"), ("fidelity", "Fidelity", "{:.3f}"), ("smoothness", "Smooth", "{:.3f}"),
    ("economy", "Economy", "{:.3f}"), ("ssim", "SSIM", "{:.3f}"), ("delta_e_mean", "ΔE mean", "{:.2f}"),
    ("delta_e_p95", "ΔE p95", "{:.1f}"), ("edge_f1", "Edge F1", "{:.3f}"), ("alpha_mae", "α err", "{:.3f}"),
    ("banding_index", "Banding", "{:.2f}"), ("outline_px", "Outline px", "{:.3f}"), ("junction_px", "Junction px", "{:.3f}"),
    ("line_debt_px", "Line debt", "{:.0f}"), ("paths", "Paths", "{:.0f}"), ("bytes", "Bytes", "{:.0f}"),
    ("elapsed_ms", "ms", "{:.0f}"),
]
ITEM_COLS = [c for c in SUMMARY_COLS if c[0] not in ("economy",)]

CSS = """
:root{color-scheme:light;--background:0 0% 99%;--foreground:222 47% 11%;--card:0 0% 100%;--muted:210 40% 96%;
--muted-fg:215 16% 47%;--primary:215 20% 30%;--primary-fg:58 100% 74%;--border:214 32% 91%;--danger:0 78% 58%;
--success:142 62% 38%;--yellow:58 100% 74%;--radius:10px;font-family:Poppins,ui-sans-serif,system-ui,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--background:210 15% 15%;--foreground:210 20% 98%;
--card:214 19% 18%;--muted:210 12% 22%;--muted-fg:215 15% 65%;--primary:215 20% 45%;--border:210 12% 25%}}
*{box-sizing:border-box}body{margin:0;background:hsl(var(--background));color:hsl(var(--foreground));font-size:14px;padding:24px 16px}
main{max-width:1280px;margin:0 auto}h1{font-size:24px;letter-spacing:-.03em;margin:0 0 4px}h2{font-size:16px;margin:32px 0 8px}
p,.muted{color:hsl(var(--muted-fg));margin:0}.card{background:hsl(var(--card));border:1px solid hsl(var(--border));border-radius:var(--radius);
padding:12px 16px;margin:12px 0;overflow:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid hsl(var(--border));white-space:nowrap}th{color:hsl(var(--muted-fg));font-weight:500;font-size:12px}
td:first-child,th:first-child,td.l,th.l{text-align:left}.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border-radius:999px;background:hsl(var(--muted));font-size:12px}
.dot{width:6px;height:6px;border-radius:50%;background:hsl(var(--yellow))}.best{font-weight:600;background:hsl(var(--yellow)/.25)}
.thumbs{display:flex;gap:8px}.thumbs figure{margin:0;text-align:center}.thumbs img{display:block;width:120px;height:120px;object-fit:contain;
background:repeating-conic-gradient(hsl(var(--muted)) 0 25%,transparent 0 50%) 0 0/16px 16px;border:1px solid hsl(var(--border));border-radius:6px}
.thumbs figcaption{font-size:11px;color:hsl(var(--muted-fg));margin-top:2px}.err{color:hsl(var(--danger))}details{margin:8px 0}summary{cursor:pointer;font-weight:500}
code{font-family:"SFMono-Regular",Consolas,monospace;font-size:12px}
"""


def _fmt(v, fmt: str) -> str:
    if v is None:
        return "–"
    try:
        return fmt.format(v)
    except (ValueError, TypeError):
        return html.escape(str(v))


def _summary_table(results: dict, cls: str) -> str:
    engines = sorted(results["summary"].keys())
    rows = [(e, results["summary"][e].get(cls)) for e in engines]
    rows = [(e, s) for e, s in rows if s]
    if not rows:
        return ""
    best_score = max(s.get("score", float("-inf")) for _, s in rows)
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label, _ in SUMMARY_COLS)
    body = ""
    for e, s in rows:
        cells = "".join(
            f'<td class="{"best" if key == "score" and s.get("score") == best_score else ""}">{_fmt(s.get(key), fmt)}</td>'
            for key, _, fmt in SUMMARY_COLS
        )
        errs = f' <span class="err">{s["errors"]} err</span>' if s.get("errors") else ""
        body += f'<tr><td class="l"><span class="pill"><span class="dot"></span>{html.escape(e)}</span>{errs}</td>{cells}</tr>'
    return f'<h2>{html.escape(cls)}</h2><div class="card"><table><thead><tr><th class="l">Engine</th>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _item_block(item_id: str, records: list[dict], media: dict | None) -> str:
    out = f'<details><summary><code>{html.escape(item_id)}</code></summary><div class="card">'
    for r in records:
        e = r["engine"]
        key = f"{item_id}|{e}"
        m = (media or {}).get(key, {})
        out += f'<div style="display:flex;gap:16px;align-items:flex-start;padding:8px 0;border-bottom:1px solid hsl(var(--border))">'
        out += f'<div style="min-width:90px"><span class="pill"><span class="dot"></span>{html.escape(e)}</span></div>'
        if m:
            out += '<div class="thumbs">'
            for k, cap in (("src", "source"), ("out", "trace"), ("heat", "ΔE")):
                if k in m:
                    out += f'<figure><img src="{m[k]}" alt="{cap}"><figcaption>{cap}</figcaption></figure>'
            out += "</div>"
        if "error" in r:
            out += f'<p class="err">{html.escape(r["error"])}</p>'
        else:
            cells = "".join(f"<tr><th>{html.escape(label)}</th><td>{_fmt(r['metrics'].get(key), fmt)}</td></tr>" for key, label, fmt in ITEM_COLS)
            out += f'<table style="width:auto"><tbody>{cells}</tbody></table>'
        out += "</div>"
    return out + "</div></details>"


def render_html(results: dict, media: dict | None = None) -> str:
    title = f"Vexel Bench · {results.get('label', 'run')}"
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p>{html.escape(results.get('created_utc', ''))} · {len(results.get('items', []))} traces · "
        f"{results.get('wall_seconds', 0):.1f}s · engines: {', '.join(html.escape(e) for e in results.get('engines', {}))}</p>",
    ]
    for cls in list(CLASSES) + sorted({r["cls"] for r in results["items"]} - set(CLASSES)):
        parts.append(_summary_table(results, cls))

    parts.append("<h2>Items</h2>")
    by_item: dict[str, list[dict]] = {}
    for r in results["items"]:
        by_item.setdefault(r["id"], []).append(r)
    for item_id in sorted(by_item):
        parts.append(_item_block(item_id, by_item[item_id], media))

    parts.append("<h2>Configuration</h2><div class=\"card\"><pre><code>")
    parts.append(html.escape(json.dumps({"weights": results.get("weights"), "engines": results.get("engines")}, indent=2)))
    parts.append("</code></pre></div></main></body></html>")
    return "".join(parts)


def write_html(results: dict, out: Path, media: dict | None = None) -> None:
    out.write_text(render_html(results, media), encoding="utf-8")
