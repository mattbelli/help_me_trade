"use client";

import { isBuiltinTheme, type WorkspaceTheme } from "./themes";

// ── Types ────────────────────────────────────────────────────────────────────

export type CustomTheme = {
  id: string; // "custom-<8 hex>" — also the data-theme value & css selector
  name: string;
  base: WorkspaceTheme | string; // clone source (label + delete-fallback)
  colorScheme: "dark" | "light";
  vars: Record<string, string>; // resolved knob (+ chrome) var map — what gets injected
  createdAt: string;
  updatedAt: string;
};

// ── The editor's knob model ──────────────────────────────────────────────────
// One flat list of editable colors grouped into 4 titled sections. These are the
// ONLY vars the editor writes; every legacy/shadcn var derives from them in
// globals.css (:root aliases), so editing one knob cascades everywhere it drives.
// Kept in sync with the [data-theme] blocks in globals.css.

export type ThemeKnob = {
  var: string;
  label: string;
  /** One line on what this knob visibly drives (omit when the label says it). */
  desc?: string;
};

export type ThemeSection = { title: string; knobs: ThemeKnob[] };

export const THEME_SECTIONS: ThemeSection[] = [
  {
    title: "Surface",
    knobs: [
      { var: "--surface-canvas", label: "Canvas", desc: "deepest app background" },
      { var: "--surface-grid-dot", label: "Grid dots", desc: "workspace dot grid (shown faint)" },
      { var: "--surface-toolbar", label: "Toolbars", desc: "top toolbar & bottom bar" },
      { var: "--surface-header", label: "Header", desc: "each widget's title bar" },
      { var: "--surface-widget", label: "Widget body", desc: "widget / card / popover surface" },
      { var: "--surface-hover", label: "Hover", desc: "row & control hover fill" },
      { var: "--surface-selected", label: "Selection", desc: "selected row / active item fill" },
      { var: "--surface-inset", label: "Inset", desc: "sunken wells, toolbar tiles, rails" },
    ],
  },
  {
    title: "Lines",
    knobs: [
      { var: "--line-border", label: "Border", desc: "element outlines" },
      { var: "--line-divider", label: "Divider", desc: "inner hairlines, row separators" },
    ],
  },
  {
    title: "Chart",
    knobs: [
      { var: "--chart-bg", label: "Background", desc: "candle chart canvas" },
      { var: "--chart-grid", label: "Grid", desc: "gridlines inside charts" },
      { var: "--chart-up", label: "Candle up", desc: "up candles & volume" },
      { var: "--chart-down", label: "Candle down", desc: "down candles & volume" },
      { var: "--chart-line", label: "Line", desc: "sparklines, equity curves" },
    ],
  },
  {
    title: "Text",
    knobs: [
      { var: "--text-primary", label: "Primary", desc: "main body text" },
      { var: "--text-title", label: "Title", desc: "widget & section titles" },
      { var: "--text-header", label: "Column header", desc: "SYM · LAST · Δ% label rows" },
      { var: "--text-secondary", label: "Secondary", desc: "secondary values, timestamps" },
      { var: "--text-faint", label: "Faint", desc: "placeholders, disabled, hairline labels" },
      { var: "--text-accent", label: "Accent", desc: "tickers · links · active controls · focus ring" },
      { var: "--text-on-accent", label: "On accent", desc: "text on accent-filled buttons" },
      { var: "--pos", label: "Positive", desc: "gains · buy side · positive fills" },
      { var: "--neg", label: "Negative", desc: "losses · sell side · errors" },
      { var: "--warn", label: "Warning", desc: "warnings · earnings · console prompt" },
      { var: "--info", label: "Info", desc: "info · open-order status" },
    ],
  },
];

/** Extra per-theme chrome vars captured at clone time so a custom theme keeps
 *  its base's shadows/grain/scrim (these aren't editor knobs). */
const CHROME_VARS: string[] = [
  "--border-strong",
  "--panel-shadow",
  "--widget-hover-shadow",
  "--scrim",
  "--c-accent-soft",
  "--grain",
  "--grain-opacity",
];

// The film-grain noise tile (same SVG the built-in Ember theme uses) — set as
// --grain whenever the editor's grain slider goes above 0, so ANY theme can
// opt into texture, not just clones of Ember.
export const GRAIN_URL =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='128' height='128'%3E%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='matrix' values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0.33 0.33 0.33 0 0'/%3E%3C/filter%3E%3Crect width='128' height='128' filter='url(%23g)'/%3E%3C/svg%3E\")";

const KNOB_VARS: string[] = THEME_SECTIONS.flatMap((s) =>
  s.knobs.map((k) => k.var),
);

/** True when a value can round-trip through <input type="color"> (#rgb/#rrggbb). */
export function isHexColor(value: string): boolean {
  return /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test((value ?? "").trim());
}

// Every var the inline preview may touch — cleared verbatim on cancel/save.
// Grain rides along so the editor's texture slider previews live.
const PREVIEW_VARS: string[] = [...KNOB_VARS, "--grain", "--grain-opacity"];

// ── Known theme vars (captured verbatim at clone time) ───────────────────────
export const KNOWN_THEME_VARS: string[] = [...KNOB_VARS, ...CHROME_VARS];

// ── Helpers ──────────────────────────────────────────────────────────────────

export function newCustomThemeId(): string {
  const hex =
    typeof crypto !== "undefined" && crypto.getRandomValues
      ? Array.from(crypto.getRandomValues(new Uint8Array(4)))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("")
      : Math.random().toString(16).slice(2, 10).padStart(8, "0");
  return `custom-${hex}`;
}

export function normalizeHex(value: string): string {
  const v = (value ?? "").trim().toLowerCase();
  if (/^#[0-9a-f]{6}$/.test(v)) return v;
  if (/^#[0-9a-f]{3}$/.test(v)) {
    return `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`;
  }
  return "#000000";
}

function parseColorToRgb(color: string): [number, number, number] | null {
  const v = (color ?? "").trim();
  if (/^#[0-9a-f]{3,6}$/i.test(v)) {
    const hex = normalizeHex(v);
    return [
      Number.parseInt(hex.slice(1, 3), 16),
      Number.parseInt(hex.slice(3, 5), 16),
      Number.parseInt(hex.slice(5, 7), 16),
    ];
  }
  const rgbMatch = v.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
  if (rgbMatch) {
    return [
      Number.parseFloat(rgbMatch[1]),
      Number.parseFloat(rgbMatch[2]),
      Number.parseFloat(rgbMatch[3]),
    ];
  }
  return null;
}

/** Derive color-scheme from background luminance (WCAG relative luminance). */
export function colorSchemeFromBackground(background: string): "dark" | "light" {
  const rgb = parseColorToRgb(background);
  if (!rgb) return "dark";
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance > 0.5 ? "light" : "dark";
}

/** Read the current resolved theme vars off <html>. */
export function captureThemeVars(): Record<string, string> {
  const out: Record<string, string> = {};
  if (typeof window === "undefined") return out;
  const cs = getComputedStyle(document.documentElement);
  for (const name of KNOWN_THEME_VARS) {
    const raw = cs.getPropertyValue(name).trim();
    if (raw) out[name] = raw;
  }
  return out;
}

/** Resolve a theme's vars WITHOUT visibly switching to it: flip <html>'s
 *  data-theme synchronously, read computed vars, flip back — no frame renders
 *  in between, and the :root + [data-theme] cascade resolves exactly as if the
 *  theme were active (a probe div would wrongly inherit the ACTIVE theme's
 *  values for vars the target doesn't declare). Powers "edit" on built-ins —
 *  seeds a clone from the target's own palette. */
export function captureThemeVarsFor(themeId: string): Record<string, string> {
  if (typeof window === "undefined") return {};
  const html = document.documentElement;
  const prev = html.getAttribute("data-theme");
  html.setAttribute("data-theme", themeId);
  try {
    return captureThemeVars();
  } finally {
    if (prev == null) html.removeAttribute("data-theme");
    else html.setAttribute("data-theme", prev);
  }
}

/** The CSS var declarations for one theme's var map. */
function varBlock(vars: Record<string, string>): string {
  return KNOWN_THEME_VARS.filter((name) => vars[name] != null)
    .map((name) => `${name}:${vars[name]};`)
    .join("");
}

/** a×pct + b×(1-pct), hex out; null when either side won't parse. */
function mixHex(a: string, b: string, aPct: number): string | null {
  const pa = parseColorToRgb(a);
  const pb = parseColorToRgb(b);
  if (!pa || !pb) return null;
  return `#${pa
    .map((c, i) => Math.round(c * aPct + pb[i] * (1 - aPct)))
    .map((c) => Math.min(255, Math.max(0, c)).toString(16).padStart(2, "0"))
    .join("")}`;
}

// Custom themes saved before a knob existed carry no value for it, so it
// would fall back to :root's carbon color — off-palette on the custom theme
// (the founder's "midnight oil" showed carbon-teal selections). Derive the
// missing selection surface from the theme's OWN accent over its widget
// surface (~12%, matching the built-in defaults). The editor captures
// computed vars, so the derived value shows up in the Selection knob and
// bakes into the theme on its next save.
function withDerivedVars(vars: Record<string, string>): Record<string, string> {
  if (vars["--surface-selected"]) return vars;
  const derived = mixHex(
    vars["--text-accent"] ?? "",
    vars["--surface-widget"] ?? "",
    0.12,
  );
  return derived ? { ...vars, "--surface-selected": derived } : vars;
}

export function buildCustomThemeCss(themes: CustomTheme[]): string {
  // Themes saved before newer knobs/chrome vars existed carry no value for
  // them, so those vars would cascade to :root's CARBON colors — off-palette
  // on any non-carbon base (the midnight-oil "doesn't work" class of bug).
  // Backfill every missing var from the theme's own BASE built-in instead;
  // the theme's saved values always win. Captured once per base per rebuild
  // (injection runs on hydrate + save, not per frame).
  const baseCache = new Map<string, Record<string, string>>();
  const baseVarsFor = (id: string | undefined): Record<string, string> | null => {
    if (!isBuiltinTheme(id) || typeof window === "undefined") return null;
    let vars = baseCache.get(id);
    if (!vars) {
      vars = captureThemeVarsFor(id);
      baseCache.set(id, vars);
    }
    return vars;
  };
  return themes
    .map((t) => {
      const base = baseVarsFor(t.base);
      const vars = base ? { ...base, ...t.vars } : t.vars;
      return `[data-theme="${t.id}"]{color-scheme:${t.colorScheme};${varBlock(withDerivedVars(vars))}}`;
    })
    .join("\n");
}

export const CUSTOM_STYLE_ID = "hmt-custom-themes";

/** Fired whenever theme CSS vars change at runtime (custom-theme inject / live preview / clear), so canvas consumers (charts) can re-read colors. */
export const THEME_VARS_EVENT = "hmt-theme-vars";

/** Layer A: create/update the persistent <style> rule per saved custom theme. */
export function injectCustomThemeStyles(themes: CustomTheme[]): void {
  if (typeof document === "undefined") return;
  let el = document.getElementById(CUSTOM_STYLE_ID) as HTMLStyleElement | null;
  if (!el) {
    el = document.createElement("style");
    el.id = CUSTOM_STYLE_ID;
    document.head.appendChild(el);
  }
  el.textContent = buildCustomThemeCss(themes);
  window.dispatchEvent(new Event(THEME_VARS_EVENT));
}

// Live color edits fire many times/sec (dragging the native color picker); each
// apply sets ~25 CSS vars on <html> (a full-app style recalc) and pings chart
// canvases to redraw. Coalesce to one apply per animation frame so the picker
// stays smooth (this was the Edge/Chromium color-picker lag).
let _previewRaf: number | null = null;
let _pendingPreview: Record<string, string> | null = null;

function flushPreview(): void {
  if (_previewRaf !== null) {
    cancelAnimationFrame(_previewRaf);
    _previewRaf = null;
  }
  const vars = _pendingPreview;
  _pendingPreview = null;
  if (!vars || typeof document === "undefined") return;
  const style = document.documentElement.style;
  for (const name of PREVIEW_VARS) {
    if (vars[name] != null) style.setProperty(name, vars[name]);
  }
  window.dispatchEvent(new Event(THEME_VARS_EVENT));
}

/** Layer B: transient inline overlay on <html> (live editing only). rAF-coalesced. */
export function applyInlinePreview(vars: Record<string, string>): void {
  if (typeof document === "undefined") return;
  _pendingPreview = vars;
  if (_previewRaf !== null) return;
  _previewRaf = requestAnimationFrame(flushPreview);
}

/** Apply any pending preview synchronously — call before capturing vars on save. */
export function flushInlinePreview(): void {
  flushPreview();
}

export function clearInlinePreview(): void {
  if (typeof document === "undefined") return;
  if (_previewRaf !== null) {
    cancelAnimationFrame(_previewRaf);
    _previewRaf = null;
  }
  _pendingPreview = null;
  const style = document.documentElement.style;
  for (const name of PREVIEW_VARS) style.removeProperty(name);
  window.dispatchEvent(new Event(THEME_VARS_EVENT));
}


/** Whether the active theme should get the `.dark` class (for @custom-variant dark). */
export function resolveThemeIsDark(
  themeId: string | undefined | null,
  customThemes: CustomTheme[],
): boolean {
  if (
    !themeId ||
    themeId === "carbon" ||
    themeId === "lab" ||
    themeId === "warm" ||
    themeId === "oled"
  ) {
    return themeId !== "light";
  }
  if (themeId === "light") return false;

  const custom = customThemes.find((t) => t.id === themeId);
  if (custom) {
    if (isBuiltinTheme(custom.base)) {
      return custom.base !== "light";
    }
    return custom.colorScheme === "dark";
  }

  if (typeof document !== "undefined") {
    const bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--surface-canvas")
      .trim();
    if (bg) return colorSchemeFromBackground(bg) === "dark";
  }
  return true;
}

/** Preview darkness while the theme editor is open (clone base or edited canvas). */
export function resolveEditorPreviewIsDark(
  cloneBase: string,
  vars: Record<string, string>,
): boolean {
  if (isBuiltinTheme(cloneBase)) {
    return cloneBase !== "light";
  }
  return colorSchemeFromBackground(vars["--surface-canvas"] ?? "#000000") === "dark";
}

export function syncHtmlDarkClass(isDark: boolean): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", isDark);
}

// ── localStorage ─────────────────────────────────────────────────────────────

// v2: the knob rename (2026-07-12) is a clean break — v1 entries stored old var
// names and are dropped rather than half-loaded.
export const CUSTOM_THEMES_STORAGE_KEY = "hmt-custom-themes-v2";
const MAX_CUSTOM_THEMES = 24;

export function loadLocalCustomThemes(): CustomTheme[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(CUSTOM_THEMES_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isValidCustomTheme).slice(0, MAX_CUSTOM_THEMES);
  } catch {
    return [];
  }
}

export function saveLocalCustomThemes(themes: CustomTheme[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      CUSTOM_THEMES_STORAGE_KEY,
      JSON.stringify(themes),
    );
  } catch {
    // ignore quota / private mode
  }
}

function isValidCustomTheme(t: unknown): t is CustomTheme {
  if (!t || typeof t !== "object") return false;
  const o = t as Record<string, unknown>;
  return (
    typeof o.id === "string" &&
    typeof o.name === "string" &&
    o.vars != null &&
    typeof o.vars === "object"
  );
}
