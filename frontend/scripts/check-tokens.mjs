#!/usr/bin/env node
// Fails the build when a `var(--x)` names a CSS custom property nothing defines.
// An invented token is silent in the browser — the declaration just does nothing —
// so this is the only thing that catches it before the founder sees a blank cell.
//
// Definitions come from four places, all real in this codebase:
//   1. `--x:` in any .css under app/            (globals.css: :root + every [data-theme])
//   2. React style objects   `"--x": value`     (reasoning.tsx --animation-duration, toggle-group --gap)
//   3. Tailwind arbitrary props `[--x:value]`   (shimmer.tsx --bg)
//   4. `setProperty("--x", …)` with a literal name
// Known limitation: 2–4 are element-scoped in reality but counted globally here.
// So is `resolveCssColor("--chart-grid")`-style JS token reads — those are
// references this gate does not check. Both are fine: the gate under-reports,
// it never invents a failure.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const FRONTEND = new URL("..", import.meta.url).pathname;
const DEF_CSS_DIRS = ["app"];
const SCAN_DIRS = ["app", "components", "lib"];

// Names some third party writes onto the DOM at runtime, so they can never
// appear as a definition in our source. Prefix match.
// (The only dynamic `setProperty(name, …)` we own — lib/workspace/custom-themes.ts,
// looping over PREVIEW_VARS — injects theme knobs that globals.css already
// defines, so it needs no entry here.)
const ALLOWED_PREFIXES = [
  // Shiki dual-theme highlighting stamps style="--shiki-dark:#…" on every token
  // span it renders (@shikijs/core, cssVariablePrefix default "--shiki-").
  "--shiki-",
];

function walk(dir, exts, out = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) walk(path, exts, out);
    else if (exts.some((e) => entry.endsWith(e))) out.push(path);
  }
  return out;
}

const defined = new Set();
for (const dir of DEF_CSS_DIRS)
  for (const file of walk(join(FRONTEND, dir), [".css"]))
    for (const m of readFileSync(file, "utf8").matchAll(/(?:^|[;{\s])(--[\w-]+)\s*:/g))
      defined.add(m[1]);

const RUNTIME_DEFS = [
  /["'`](--[\w-]+)["'`]\s*:/g, // React style object key
  /\[(--[\w-]+)\s*:/g, // Tailwind arbitrary property
  /setProperty\(\s*["'`](--[\w-]+)["'`]/g,
];
for (const dir of SCAN_DIRS)
  for (const file of walk(join(FRONTEND, dir), [".ts", ".tsx"])) {
    const src = readFileSync(file, "utf8");
    for (const re of RUNTIME_DEFS) for (const m of src.matchAll(re)) defined.add(m[1]);
  }

const problems = [];
let refs = 0;
for (const dir of SCAN_DIRS)
  for (const file of walk(join(FRONTEND, dir), [".ts", ".tsx", ".css"]))
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => {
        for (const m of line.matchAll(/var\(\s*(--[\w-]+)/g)) {
          refs++;
          const name = m[1];
          if (defined.has(name)) continue;
          if (ALLOWED_PREFIXES.some((p) => name.startsWith(p))) continue;
          problems.push(`${relative(FRONTEND, file)}:${i + 1}  var(${name})`);
        }
      });

if (problems.length) {
  for (const p of problems) console.error(p);
  console.error(`\ncheck-tokens FAILED — ${problems.length} reference(s) to undefined CSS custom properties`);
  process.exit(1);
}
console.log(`check-tokens OK — ${refs} var() references, all ${defined.size} names defined`);
