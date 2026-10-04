#!/usr/bin/env node
// Writes THIRD_PARTY_NOTICES.html: the Rust section cargo-about generated (argv[2]), the frontend's production npm
// packages and the bundled fonts. Usage: node npm-notices.mjs <rust-section.html> <out.html>
// Deterministic: sorted by name then version, no timestamps, no absolute paths.
import { existsSync, readdirSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const frontend = join(root, "frontend");
const [rustFile, outFile] = process.argv.slice(2);
if (!rustFile || !outFile) {
  console.error("usage: npm-notices.mjs <rust-section.html> <out.html>");
  process.exit(2);
}

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const readJson = (p) => JSON.parse(readFileSync(p, "utf8"));

/** The directory of dependency `name` as Node would resolve it from `from`: the nearest node_modules up the tree. */
function resolveDep(from, name) {
  for (let dir = from; ; dir = dirname(dir)) {
    const candidate = join(dir, "node_modules", name);
    if (existsSync(join(candidate, "package.json"))) return candidate;
    if (dir === frontend || dir === dirname(dir)) return null;
  }
}

/** Every package the frontend's `dependencies` reach, as { dir, pkg }, keyed by directory. */
function productionTree() {
  const seen = new Map();
  const walk = (dir, pkg) => {
    for (const name of Object.keys(pkg.dependencies ?? {})) {
      const found = resolveDep(dir, name);
      if (!found || seen.has(found)) continue;
      const child = readJson(join(found, "package.json"));
      seen.set(found, child);
      walk(found, child);
    }
  };
  walk(frontend, readJson(join(frontend, "package.json")));
  return [...seen].map(([dir, pkg]) => ({ dir, pkg }));
}

function licenceField(pkg) {
  const l = pkg.license ?? pkg.licenses;
  if (typeof l === "string") return l;
  if (l && typeof l === "object" && !Array.isArray(l)) return l.type ?? "";
  if (Array.isArray(l)) return l.map((x) => x.type ?? x).join(" OR ");
  return "";
}

/** The text of the package's LICENSE*, LICENCE* or COPYING* file (the first by name), or "". */
function licenceText(dir) {
  const names = readdirSync(dir).filter((n) => /^(licen[sc]e|copying)([-._].*)?$/i.test(n)).sort();
  for (const n of names) {
    try {
      return readFileSync(join(dir, n), "utf8").replace(/\r\n/g, "\n").trim();
    } catch {
      /* a directory */
    }
  }
  return "";
}

const FONT_PACKAGE = "@fontsource/poppins";
const packages = productionTree()
  .map(({ dir, pkg }) => ({ name: pkg.name, version: pkg.version, licence: licenceField(pkg), text: licenceText(dir) }))
  .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : a.version < b.version ? -1 : a.version > b.version ? 1 : 0));

const fontDir = resolveDep(frontend, FONT_PACKAGE);
if (!fontDir) throw new Error(`${FONT_PACKAGE} is not installed: run npm ci in frontend/`);
const fontPkg = readJson(join(fontDir, "package.json"));
const fontText = licenceText(fontDir);
if (!fontText) throw new Error(`${FONT_PACKAGE} carries no licence file`);

const npmEntries = packages
  .map((p) => {
    const body = p.name === FONT_PACKAGE ? `<p class="note">Full licence text under Fonts.</p>` : p.text ? `<pre>${esc(p.text)}</pre>` : `<p class="note">No licence file is shipped with this package.</p>`;
    return `<article>\n<h3>${esc(p.name)} <span class="ver">${esc(p.version)}</span></h3>\n<p class="used">Licence: ${esc(p.licence || "not stated")}</p>\n${body}\n</article>`;
  })
  .join("\n");

const rust = readFileSync(rustFile, "utf8").trim();

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Studi0Trace third-party notices</title>
<style>
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1d1d1f; --muted: #6e6e73; --rule: #d2d2d7; --code: #f5f5f7; }
@media (prefers-color-scheme: dark) { :root { --bg: #1c1c1e; --fg: #f5f5f7; --muted: #98989d; --rule: #3a3a3c; --code: #2c2c2e; } }
body { margin: 0 auto; max-width: 52rem; padding: 2.5rem 1.25rem 4rem; background: var(--bg); color: var(--fg); font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif; }
h1 { font-size: 1.75rem; margin: 0 0 .5rem; }
h2 { font-size: 1.25rem; margin: 2.5rem 0 .75rem; padding-top: 1rem; border-top: 1px solid var(--rule); }
h3 { font-size: 1rem; margin: 1.5rem 0 .25rem; }
.ver, .used, .note, .lead { color: var(--muted); }
.ver { font-weight: 400; }
.used { margin: .25rem 0; font-size: .9rem; }
ul.used { padding-left: 1.25rem; columns: 2; }
ul.overview { padding-left: 1.25rem; }
pre { background: var(--code); border-radius: 6px; padding: .75rem 1rem; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
</head>
<body>
<h1>Third-party notices</h1>
<p class="lead">Studi0Trace for Mac is MIT licensed. It includes the open-source software listed here: the Rust crates it is built from, the packages of its interface and the font it ships.</p>
${rust}
<section id="npm">
<h2>Interface packages</h2>
<p>The production dependencies of the interface (React, Radix UI and the packages they use), ${packages.length} in all.</p>
${npmEntries}
</section>
<section id="fonts">
<h2>Fonts</h2>
<h3>Poppins <span class="ver">${esc(fontPkg.version)}</span></h3>
<p class="used">Poppins by the Poppins Project Authors, bundled through ${esc(FONT_PACKAGE)}. Licence: SIL Open Font License 1.1 (${esc(licenceField(fontPkg))}), https://openfontlicense.org</p>
<pre>${esc(fontText)}</pre>
</section>
</body>
</html>
`;

mkdirSync(dirname(resolve(outFile)), { recursive: true });
writeFileSync(outFile, html);
console.log(`${outFile}: ${packages.length} npm packages, ${(html.length / 1024).toFixed(0)} KiB`);
