"use client";

import type { WorkspaceTheme } from "./themes";

// ── Types ────────────────────────────────────────────────────────────────────

export type EditableToken =
  | "bg"
  | "surface"
  | "border"
  | "fg"
  | "fgDim"
  | "accent"
  | "green"
  | "red"
  | "chartBg"
  | "chartAccent";

export type CustomTheme = {
  id: string; // "custom-<8 hex>" — also the data-theme value & css selector
  name: string;
  base: WorkspaceTheme | string; // clone source (label + delete-fallback)
  colorScheme: "dark" | "light";
  tokens: Record<EditableToken, string>; // hex per control (for re-opening editor)
  vars: Record<string, string>; // FULL resolved var map (what gets injected)
  createdAt: string;
  updatedAt: string;
};

// ── Editable token → CSS-var fan-out ─────────────────────────────────────────
// Derived from the equalities in the carbon block (globals.css). The first var
// in each list is the "representative" read back when re-seeding the editor.

export const EDITABLE_TOKENS: {
  token: EditableToken;
  label: string;
  vars: string[];
}[] = [
  { token: "bg", label: "Background", vars: ["--background"] },
  {
    token: "surface",
    label: "Surface",
    vars: [
      "--panel",
      "--card",
      "--popover",
      "--bg-raised",
      "--bg-hover",
      "--bg-inset",
      "--muted",
      "--secondary",
      "--accent",
      "--sidebar",
      "--sidebar-accent",
    ],
  },
  {
    token: "border",
    label: "Border",
    vars: ["--border", "--input", "--sidebar-border", "--border-strong"],
  },
  {
    token: "fg",
    label: "Text",
    vars: [
      "--foreground",
      "--card-foreground",
      "--popover-foreground",
      "--primary",
      "--secondary-foreground",
      "--accent-foreground",
      "--sidebar-foreground",
      "--sidebar-accent-foreground",
    ],
  },
  {
    token: "fgDim",
    label: "Text dim",
    vars: ["--fg-dim", "--muted-foreground", "--fg-faint"],
  },
  {
    token: "accent",
    label: "Accent",
    vars: [
      "--c-accent",
      "--ring",
      "--sidebar-primary",
      "--sidebar-ring",
      "--chart-1",
    ],
  },
  { token: "green", label: "Positive", vars: ["--c-green", "--pnl-positive"] },
  {
    token: "red",
    label: "Negative",
    vars: [
      "--c-red",
      "--pnl-negative",
      "--destructive",
      "--chart-down",
      "--c-danger",
    ],
  },
  { token: "chartBg", label: "Chart bg", vars: ["--chart-background"] },
  { token: "chartAccent", label: "Chart line", vars: ["--chart-accent"] },
];

const REPRESENTATIVE_VAR: Record<EditableToken, string> = Object.fromEntries(
  EDITABLE_TOKENS.map((t) => [t.token, t.vars[0]]),
) as Record<EditableToken, string>;

// ── Full per-var catalog (the "All colors" section) ──────────────────────────
// Every themable var, individually editable, grouped for the editor. Kept in
// sync with the [data-theme] blocks in globals.css; excludes the Tailwind
// --color-* aliases, font/radius vars, and the non-color --panel-shadow.

export type ColorVar = { var: string; label: string };
export type ColorVarGroup = { label: string; vars: ColorVar[] };

export const ALL_COLOR_GROUPS: ColorVarGroup[] = [
  {
    label: "Text",
    vars: [
      { var: "--foreground", label: "Foreground" },
      { var: "--fg-dim", label: "Dim text" },
      { var: "--fg-faint", label: "Faint text" },
      { var: "--muted-foreground", label: "Muted text" },
    ],
  },
  {
    label: "Surfaces",
    vars: [
      { var: "--background", label: "Background" },
      { var: "--panel", label: "Panel" },
      { var: "--card", label: "Card" },
      { var: "--popover", label: "Popover" },
      { var: "--bg-raised", label: "Raised" },
      { var: "--bg-hover", label: "Hover" },
      { var: "--bg-inset", label: "Inset" },
      { var: "--muted", label: "Muted" },
      { var: "--secondary", label: "Secondary" },
      { var: "--accent", label: "Accent surface" },
      { var: "--scrim", label: "Scrim (overlay)" },
    ],
  },
  {
    label: "Borders",
    vars: [
      { var: "--border", label: "Border" },
      { var: "--border-strong", label: "Border strong" },
      { var: "--input", label: "Input" },
      { var: "--ring", label: "Focus ring" },
    ],
  },
  {
    label: "Accent",
    vars: [
      { var: "--c-accent", label: "Accent" },
      { var: "--c-accent-soft", label: "Accent soft" },
      { var: "--c-on-accent", label: "On accent" },
      { var: "--c-glow", label: "Glow" },
      { var: "--c-command", label: "Command" },
    ],
  },
  {
    label: "Semantic",
    vars: [
      { var: "--c-green", label: "Green" },
      { var: "--pnl-positive", label: "P&L positive" },
      { var: "--c-red", label: "Red" },
      { var: "--pnl-negative", label: "P&L negative" },
      { var: "--c-danger", label: "Danger" },
      { var: "--destructive", label: "Destructive" },
      { var: "--c-amber", label: "Amber (warnings)" },
      { var: "--c-yellow", label: "Yellow" },
      { var: "--c-blue", label: "Blue" },
      { var: "--c-violet", label: "Violet" },
    ],
  },
  {
    label: "Chart",
    vars: [
      { var: "--chart-background", label: "Chart bg" },
      { var: "--chart-accent", label: "Chart line" },
      { var: "--chart-down", label: "Chart down" },
      { var: "--chart-1", label: "Chart 1" },
      { var: "--chart-2", label: "Chart 2" },
      { var: "--chart-3", label: "Chart 3" },
      { var: "--chart-4", label: "Chart 4" },
      { var: "--chart-5", label: "Chart 5" },
    ],
  },
  {
    label: "Sidebar",
    vars: [
      { var: "--sidebar", label: "Sidebar" },
      { var: "--sidebar-accent", label: "Sidebar accent" },
      { var: "--sidebar-border", label: "Sidebar border" },
      { var: "--sidebar-foreground", label: "Sidebar text" },
      { var: "--sidebar-accent-foreground", label: "Sidebar accent text" },
      { var: "--sidebar-primary", label: "Sidebar primary" },
      { var: "--sidebar-ring", label: "Sidebar ring" },
    ],
  },
];

/** True when a value can round-trip through <input type="color"> (#rgb/#rrggbb). */
export function isHexColor(value: string): boolean {
  return /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test((value ?? "").trim());
}

// Every var the inline preview may touch — cleared verbatim on cancel/save.
// Union of the quick-palette groups, the full per-var catalog, and the derived
// --c-accent-soft, deduped.
const PREVIEW_VARS: string[] = Array.from(
  new Set([
    ...EDITABLE_TOKENS.flatMap((t) => t.vars),
    ...ALL_COLOR_GROUPS.flatMap((g) => g.vars.map((v) => v.var)),
    "--c-accent-soft",
  ]),
);

// ── Known theme vars (captured verbatim at clone time) ───────────────────────
// The ~50 keys present in each globals.css [data-theme] block.

export const KNOWN_THEME_VARS: string[] = [
  "--background",
  "--chart-background",
  "--foreground",
  "--card",
  "--card-foreground",
  "--popover",
  "--popover-foreground",
  "--primary",
  "--primary-foreground",
  "--secondary",
  "--secondary-foreground",
  "--muted",
  "--muted-foreground",
  "--accent",
  "--accent-foreground",
  "--destructive",
  "--border",
  "--input",
  "--ring",
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
  "--chart-accent",
  "--pnl-positive",
  "--pnl-negative",
  "--radius",
  "--sidebar",
  "--sidebar-foreground",
  "--sidebar-primary",
  "--sidebar-primary-foreground",
  "--sidebar-accent",
  "--sidebar-accent-foreground",
  "--sidebar-border",
  "--sidebar-ring",
  "--panel",
  "--bg-raised",
  "--bg-inset",
  "--bg-hover",
  "--border-strong",
  "--fg-dim",
  "--fg-faint",
  "--c-accent",
  "--c-accent-soft",
  "--c-violet",
  "--c-blue",
  "--c-green",
  "--c-red",
  "--c-yellow",
  "--c-amber",
  "--c-command",
  "--c-danger",
  "--c-on-accent",
  "--chart-down",
  "--panel-shadow",
  "--c-glow",
  "--scrim",
  "--grid-placeholder",
];

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

/** Derive the 9 editor hex controls from a full var map. */
export function deriveTokensFromVars(
  vars: Record<string, string>,
): Record<EditableToken, string> {
  const tokens = {} as Record<EditableToken, string>;
  for (const { token } of EDITABLE_TOKENS) {
    tokens[token] = normalizeHex(vars[REPRESENTATIVE_VAR[token]] ?? "#000000");
  }
  return tokens;
}

/** Apply the token fan-out onto a base var map (+ derive --c-accent-soft). */
export function mergeTokensIntoVars(
  baseVars: Record<string, string>,
  tokens: Record<EditableToken, string>,
): Record<string, string> {
  const vars: Record<string, string> = { ...baseVars };
  for (const { token, vars: targets } of EDITABLE_TOKENS) {
    const raw = (tokens[token] ?? "").trim();
    if (!/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(raw)) continue;
    const hex = normalizeHex(raw);
    for (const v of targets) vars[v] = hex;
  }
  vars["--c-accent-soft"] =
    `color-mix(in srgb, ${normalizeHex(tokens.accent)} 12%, transparent)`;
  return vars;
}

/** The CSS var declarations for one theme's var map. */
function varBlock(vars: Record<string, string>): string {
  return KNOWN_THEME_VARS.filter((name) => vars[name] != null)
    .map((name) => `${name}:${vars[name]};`)
    .join("");
}

export function buildCustomThemeCss(themes: CustomTheme[]): string {
  return themes
    .map(
      (t) =>
        `[data-theme="${t.id}"]{color-scheme:${t.colorScheme};${varBlock(t.vars)}}`,
    )
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

/** Layer B: transient inline overlay on <html> (live editing only). */
export function applyInlinePreview(vars: Record<string, string>): void {
  if (typeof document === "undefined") return;
  const style = document.documentElement.style;
  for (const name of PREVIEW_VARS) {
    if (vars[name] != null) style.setProperty(name, vars[name]);
  }
  window.dispatchEvent(new Event(THEME_VARS_EVENT));
}

export function clearInlinePreview(): void {
  if (typeof document === "undefined") return;
  const style = document.documentElement.style;
  for (const name of PREVIEW_VARS) style.removeProperty(name);
  window.dispatchEvent(new Event(THEME_VARS_EVENT));
}

// ── localStorage ─────────────────────────────────────────────────────────────

export const CUSTOM_THEMES_STORAGE_KEY = "hmt-custom-themes-v1";
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
    typeof o.vars === "object" &&
    o.tokens != null &&
    typeof o.tokens === "object"
  );
}
