import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

import { intradayBarSession } from "@/lib/chart-time";

// Subtle session shading for intraday charts: pre-market (04:00–09:30 ET) and
// post-market (16:00–20:00 ET) candle runs get a faint full-height background
// band so ext-hours candles read as "not regular session" without any clutter
// (no labels, very low alpha — the founder hates busy charts). Regular session
// gets no tint. Drawn beneath the series (zOrder "bottom").

export type SessionBandColors = { pre: string; post: string };

type Band = { left: number; right: number; kind: "pre" | "post" };

class SessionBandsRenderer implements IPrimitivePaneRenderer {
  constructor(
    private readonly bands: Band[],
    private readonly colors: SessionBandColors,
  ) {}
  draw(target: CanvasRenderingTarget2D) {
    target.useBitmapCoordinateSpace((scope) => {
      const hp = scope.horizontalPixelRatio;
      const ctx = scope.context;
      const height = scope.bitmapSize.height;
      for (const band of this.bands) {
        const x = band.left * hp;
        const w = (band.right - band.left) * hp;
        if (w <= 0) continue;
        ctx.fillStyle = band.kind === "pre" ? this.colors.pre : this.colors.post;
        ctx.fillRect(x, 0, w, height);
      }
    });
  }
}

class SessionBandsView implements IPrimitivePaneView {
  private bands: Band[] = [];
  constructor(
    private readonly src: SessionBands,
    private readonly colors: SessionBandColors,
  ) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "bottom";
  }
  update() {
    this.bands = [];
    const { chart } = this.src;
    if (!chart) return;
    const ts = chart.timeScale();
    const bars = this.src.getBars();
    if (bars.length === 0) return;
    const half = Math.max(0.5, ts.options().barSpacing / 2);

    // Walk contiguous runs of identical session; emit a band per pre/post run.
    let runStart = 0;
    let runSession = intradayBarSession(bars[0].time);
    const flush = (start: number, end: number, session: string) => {
      if (session !== "pre" && session !== "post") return;
      const l = ts.timeToCoordinate(bars[start].time as unknown as Time);
      const r = ts.timeToCoordinate(bars[end].time as unknown as Time);
      if (l == null || r == null) return;
      this.bands.push({
        left: l - half,
        right: r + half,
        kind: session,
      });
    };
    for (let i = 1; i < bars.length; i++) {
      const s = intradayBarSession(bars[i].time);
      if (s !== runSession) {
        flush(runStart, i - 1, runSession);
        runStart = i;
        runSession = s;
      }
    }
    flush(runStart, bars.length - 1, runSession);
  }
  renderer() {
    return new SessionBandsRenderer(this.bands, this.colors);
  }
}

export class SessionBands implements ISeriesPrimitive<Time> {
  chart?: IChartApi;
  series?: ISeriesApi<SeriesType>;
  private readonly _views: SessionBandsView[];
  private _requestUpdate?: () => void;
  constructor(
    public getBars: () => readonly { time: number }[],
    colors: SessionBandColors,
  ) {
    this._views = [new SessionBandsView(this, colors)];
  }
  attached(p: SeriesAttachedParameter<Time>) {
    this.chart = p.chart;
    this.series = p.series;
    this._requestUpdate = p.requestUpdate;
  }
  detached() {
    this.chart = undefined;
    this.series = undefined;
    this._requestUpdate = undefined;
  }
  updateAllViews() {
    this._views.forEach((v) => v.update());
  }
  paneViews() {
    return this._views;
  }
  requestUpdate() {
    this._requestUpdate?.();
  }
}
