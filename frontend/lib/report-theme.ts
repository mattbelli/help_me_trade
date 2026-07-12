import { captureThemeVars } from "@/lib/workspace/custom-themes";

/** Resolve a color-mix (or other) expression to a concrete computed color. */
export function resolveComputedColor(expression: string): string {
  if (typeof document === "undefined") return expression;
  const el = document.createElement("div");
  el.style.position = "absolute";
  el.style.visibility = "hidden";
  el.style.backgroundColor = expression;
  document.body.appendChild(el);
  const resolved = getComputedStyle(el).backgroundColor;
  document.body.removeChild(el);
  return resolved || expression;
}

export function buildReportThemeOverride(): Record<string, string> {
  const app = captureThemeVars();
  const card = app["--surface-widget"] ?? "";
  const bgInset = app["--surface-inset"] ?? "";
  const bgRaised = app["--surface-toolbar"] ?? "";
  const cAmber = app["--warn"] ?? "";
  const foreground = app["--text-primary"] ?? "";

  let warnBg = bgInset;
  if (cAmber && card) {
    warnBg = resolveComputedColor(
      `color-mix(in srgb, ${cAmber} 12%, ${card})`,
    );
  }

  return {
    "--bg": app["--surface-canvas"] ?? "",
    "--card": card,
    "--text": foreground,
    "--muted": app["--text-secondary"] ?? "",
    "--accent": app["--text-accent"] ?? "",
    "--border": app["--line-border"] ?? "",
    "--hero": bgRaised,
    "--badge-bg": bgInset || bgRaised,
    "--summary-text": app["--text-secondary"] ?? "",
    "--warn-bg": warnBg,
    "--warn-border": cAmber || bgInset,
    "--warn-text": foreground,
    "--warn-strong": foreground,
  };
}

export function injectReportTheme(body: string, vars: Record<string, string>): string {
  const decl = Object.entries(vars)
    .filter(([, v]) => v)
    .map(([name, value]) => `${name}:${value}`)
    .join(";");
  if (!decl) return body;
  const style = `<style>:root{${decl}}</style>`;
  const headClose = body.indexOf("</head>");
  if (headClose !== -1) {
    return `${body.slice(0, headClose)}${style}${body.slice(headClose)}`;
  }
  return `${style}${body}`;
}
