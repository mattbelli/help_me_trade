import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  Logical,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

export type ShapeKind = "trendline" | "channel";
export type LPoint = { logical: number; price: number };
type Px = { x: number | null; y: number | null };

const HANDLE_R = 5; // handle circle radius in px (drawn only when selected)

class DrawingRenderer implements IPrimitivePaneRenderer {
  constructor(
    private readonly kind: ShapeKind,
    private readonly a1: Px,
    private readonly a2: Px,
    private readonly b1: Px,
    private readonly b2: Px,
    private readonly handles: Px[],
    private readonly color: string,
  ) {}
  draw(target: CanvasRenderingTarget2D) {
    target.useBitmapCoordinateSpace((scope) => {
      const hp = scope.horizontalPixelRatio;
      const vp = scope.verticalPixelRatio;
      const ctx = scope.context;
      const seg = (p: Px, q: Px) => {
        if (p.x == null || p.y == null || q.x == null || q.y == null) return;
        ctx.beginPath();
        ctx.moveTo(p.x * hp, p.y * vp);
        ctx.lineTo(q.x * hp, q.y * vp);
        ctx.stroke();
      };
      ctx.lineWidth = Math.max(1, Math.round(1.5 * hp));
      ctx.strokeStyle = this.color;
      seg(this.a1, this.a2);
      if (this.kind === "channel") seg(this.b1, this.b2);
      for (const h of this.handles) {
        if (h.x == null || h.y == null) continue;
        ctx.beginPath();
        ctx.arc(h.x * hp, h.y * vp, HANDLE_R * hp, 0, 2 * Math.PI);
        ctx.fillStyle = this.color;
        ctx.fill();
        ctx.lineWidth = Math.max(1, Math.round(1 * hp));
        ctx.strokeStyle = "#ffffff";
        ctx.stroke();
      }
    });
  }
}

class DrawingView implements IPrimitivePaneView {
  private a1: Px = { x: null, y: null };
  private a2: Px = { x: null, y: null };
  private b1: Px = { x: null, y: null };
  private b2: Px = { x: null, y: null };
  private handles: Px[] = [];
  constructor(private readonly src: DrawingShape) {}
  update() {
    const { series, chart } = this.src;
    if (!series || !chart) return;
    const ts = chart.timeScale();
    const X = (logical: number) => ts.logicalToCoordinate(logical as Logical);
    const Y = (price: number) => series.priceToCoordinate(price);
    this.a1 = { x: X(this.src.p1.logical), y: Y(this.src.p1.price) };
    this.a2 = { x: X(this.src.p2.logical), y: Y(this.src.p2.price) };
    if (this.src.kind === "channel") {
      this.b1 = { x: X(this.src.p1.logical), y: Y(this.src.p1.price + this.src.width) };
      this.b2 = { x: X(this.src.p2.logical), y: Y(this.src.p2.price + this.src.width) };
    }
    this.handles = this.src.selected
      ? this.src.handlePoints().map((h) => ({ x: X(h.logical), y: Y(h.price) }))
      : [];
  }
  renderer() {
    return new DrawingRenderer(
      this.src.kind, this.a1, this.a2, this.b1, this.b2, this.handles, this.src.color,
    );
  }
}

export class DrawingShape implements ISeriesPrimitive<Time> {
  chart?: IChartApi;
  series?: ISeriesApi<SeriesType>;
  selected = false;
  private readonly _views: DrawingView[];
  private _requestUpdate?: () => void;
  constructor(
    public id: number,
    public kind: ShapeKind,
    public p1: LPoint,
    public p2: LPoint,
    public width: number, // channel price offset; 0 for trendline
    public color: string,
  ) {
    this._views = [new DrawingView(this)];
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
  // Handle anchors (logical/price): trendline = [p1, p2]; channel = [p1, p2, width-handle].
  handlePoints(): LPoint[] {
    if (this.kind === "channel") {
      return [
        this.p1,
        this.p2,
        {
          logical: (this.p1.logical + this.p2.logical) / 2,
          price: (this.p1.price + this.p2.price) / 2 + this.width,
        },
      ];
    }
    return [this.p1, this.p2];
  }
}
