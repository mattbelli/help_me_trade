/** Lightweight Charts only accepts rgb/hex — not modern CSS lab()/oklch(). */

let canvasCtx: CanvasRenderingContext2D | null = null;

function canvasContext(): CanvasRenderingContext2D | null {
  if (typeof document === "undefined") return null;
  if (!canvasCtx) {
    const canvas = document.createElement("canvas");
    canvasCtx = canvas.getContext("2d");
  }
  return canvasCtx;
}

export function toChartColor(input: string, fallback = input): string {
  const ctx = canvasContext();
  if (!ctx) return fallback;
  try {
    ctx.fillStyle = "#000000";
    ctx.fillStyle = input;
    const out = ctx.fillStyle;
    return typeof out === "string" && out.length > 0 ? out : fallback;
  } catch {
    return fallback;
  }
}

export function resolveCssColor(variable: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const probe = document.createElement("span");
  probe.style.color = `var(${variable})`;
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  document.body.removeChild(probe);
  if (!resolved || resolved === "rgba(0, 0, 0, 0)") return fallback;
  return toChartColor(resolved, fallback);
}

export function withAlpha(color: string, alpha: number): string {
  const normalized = toChartColor(color, color);
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 255)
    .toString(16)
    .padStart(2, "0");

  if (normalized.startsWith("#")) {
    const hex = normalized.slice(1);
    if (hex.length === 6) return `#${hex}${a}`;
    if (hex.length === 8) return `#${hex.slice(0, 6)}${a}`;
  }

  const match = normalized.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (match) {
    return `rgba(${match[1]}, ${match[2]}, ${match[3]}, ${alpha})`;
  }

  return color;
}
