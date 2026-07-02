/**
 * CandleChart — TradingView Lightweight Charts (v4, Apache-2.0) in a WebView.
 *
 * The lib + chart scaffold are inlined as STATIC html (memoized) so the WebView
 * never reloads / re-parses the 163KB bundle. Bar data + mode are pushed in via
 * injectJavaScript after a `ready` handshake; live ticks update only the
 * rightmost point. Two modes:
 *   - inline preview: vertical drag is left for the page ScrollView (the chart
 *     only pans horizontally / pinch-zooms), so the page still scrolls.
 *   - fullControl: vertical drag pans the price axis + a crosshair OHLC legend.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { View } from 'react-native';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';

import type { Bar } from '@/api/types';
import { Skeleton, Text } from '@/components/ui';
import { colors } from '@/theme';

import { LIGHTWEIGHT_CHARTS_JS } from './lightweight-charts-src';

export type ChartMode = 'line' | 'candle';

interface CandleChartProps {
  bars: Bar[];
  mode: ChartMode;
  /** Live price — keeps the chart's rightmost point in sync with the header quote. */
  livePrice?: number | null;
  /** Full-screen interactive chart: vertical price-axis drag + crosshair legend. */
  fullControl?: boolean;
  showLegend?: boolean;
  /** Fixed height; omit to fill the parent (flex: 1). */
  height?: number;
  loading?: boolean;
}

/** ISO timestamp → UTC seconds (lightweight-charts' numeric time). */
function toSeconds(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

function buildHtml(fullControl: boolean, showLegend: boolean): string {
  const vertDrag = fullControl ? 'true' : 'false';
  const axisDrag = fullControl ? 'true' : 'false';
  return `<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<style>
  html, body { margin:0; padding:0; background:${colors.bg}; overflow:hidden; }
  #c { width:100vw; height:100vh; }
  #legend { position:absolute; top:8px; left:10px; font:12px ui-monospace,Menlo,monospace; color:${colors.textMuted}; opacity:0; pointer-events:none; transition:opacity .1s; }
</style>
</head>
<body>
<div id="c"></div>
${showLegend ? '<div id="legend"></div>' : ''}
<script>${LIGHTWEIGHT_CHARTS_JS}</script>
<script>
  var chart = LightweightCharts.createChart(document.getElementById('c'), {
    autoSize: true,
    layout: { background: { type: 'solid', color: '${colors.bg}' }, textColor: '${colors.textMuted}', fontFamily: 'ui-monospace, Menlo, monospace' },
    grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
    crosshair: { mode: ${showLegend ? 1 : 0}, vertLine: { color: '${colors.textFaint}', labelBackgroundColor: '${colors.surfaceRaised}' }, horzLine: { color: '${colors.textFaint}', labelBackgroundColor: '${colors.surfaceRaised}' } },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: ${vertDrag} },
    handleScale: { axisPressedMouseMove: ${axisDrag}, mouseWheel: true, pinch: true },
  });
  var series = null;
  var curMode = null;

  function fmtDate(t) {
    var d = new Date(t * 1000);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
           d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }

  var legend = document.getElementById('legend');
  function updateLegend(param) {
    if (!legend) return;
    if (!param || !param.time || !param.seriesData) { legend.style.opacity = 0; return; }
    var d = param.seriesData.get(series);
    if (!d) { legend.style.opacity = 0; return; }
    legend.style.opacity = 1;
    if (d.close !== undefined) {
      legend.textContent = fmtDate(param.time) + '   O ' + d.open.toFixed(2) + '  H ' + d.high.toFixed(2) + '  L ' + d.low.toFixed(2) + '  C ' + d.close.toFixed(2);
    } else {
      legend.textContent = fmtDate(param.time) + '   ' + d.value.toFixed(2);
    }
  }
  ${showLegend ? 'chart.subscribeCrosshairMove(updateLegend);' : ''}

  window.__setData = function(data, mode) {
    if (mode !== curMode) {
      if (series) chart.removeSeries(series);
      if (mode === 'candle') {
        series = chart.addCandlestickSeries({
          upColor: '${colors.positive}', downColor: '${colors.negative}',
          borderVisible: false,
          wickUpColor: '${colors.positive}', wickDownColor: '${colors.negative}',
        });
      } else {
        series = chart.addAreaSeries({
          lineColor: '${colors.accent}', lineWidth: 2,
          topColor: 'rgba(90,209,190,0.25)', bottomColor: 'rgba(90,209,190,0.0)',
          priceLineVisible: false, lastValueVisible: false,
        });
      }
      curMode = mode;
    }
    series.setData(data);
    // Default to the most recent ~120 candles (pannable to history) so dense
    // intraday intervals don't cram hundreds of candles into the width.
    var n = data.length;
    if (n > 140) {
      chart.timeScale().setVisibleLogicalRange({ from: n - 120, to: n + 1 });
    } else {
      chart.timeScale().fitContent();
    }
  };

  // Live tick: update the rightmost point so the chart edge tracks the live price.
  window.__setLast = function(point) {
    if (series && point) series.update(point);
  };

  function ready() {
    if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'ready' }));
  }
  ready();
</script>
</body>
</html>`;
}

export function CandleChart({
  bars,
  mode,
  livePrice,
  fullControl = false,
  showLegend = false,
  height,
  loading,
}: CandleChartProps) {
  const webRef = useRef<WebView>(null);
  const [ready, setReady] = useState(false);
  const html = useMemo(() => buildHtml(fullControl, showLegend), [fullControl, showLegend]);

  const push = useCallback(() => {
    if (!webRef.current || bars.length === 0) return;
    const data =
      mode === 'candle'
        ? bars.map((b) => ({ time: toSeconds(b.time), open: b.open, high: b.high, low: b.low, close: b.close }))
        : bars.map((b) => ({ time: toSeconds(b.time), value: b.close }));
    webRef.current.injectJavaScript(`window.__setData(${JSON.stringify(data)}, ${JSON.stringify(mode)}); true;`);
  }, [bars, mode]);

  // (Re)push the full series whenever ready or data/mode changes.
  useEffect(() => {
    if (ready) push();
  }, [ready, push]);

  // Live tick → update only the rightmost point so the chart edge matches the
  // header price. Merges the live price into the last bar (forming candle).
  useEffect(() => {
    if (!ready || !webRef.current || livePrice == null || bars.length === 0) return;
    const last = bars[bars.length - 1];
    const point =
      mode === 'candle'
        ? {
            time: toSeconds(last.time),
            open: last.open,
            high: Math.max(last.high, livePrice),
            low: Math.min(last.low, livePrice),
            close: livePrice,
          }
        : { time: toSeconds(last.time), value: livePrice };
    webRef.current.injectJavaScript(`window.__setLast(${JSON.stringify(point)}); true;`);
  }, [ready, livePrice, bars, mode]);

  const onMessage = useCallback((e: WebViewMessageEvent) => {
    try {
      if (JSON.parse(e.nativeEvent.data)?.type === 'ready') setReady(true);
    } catch {
      // ignore
    }
  }, []);

  const containerStyle = height != null ? { height } : { flex: 1 };

  if (!loading && bars.length === 0) {
    return (
      <View style={[containerStyle, { alignItems: 'center', justifyContent: 'center' }]}>
        <Text variant="caption" color="textFaint">
          No chart data
        </Text>
      </View>
    );
  }

  return (
    <View style={containerStyle}>
      {loading && !ready ? (
        <View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
          <Skeleton height={height ?? 240} />
        </View>
      ) : null}
      <WebView
        ref={webRef}
        source={{ html }}
        originWhitelist={['*']}
        onMessage={onMessage}
        scrollEnabled={false}
        bounces={false}
        overScrollMode="never"
        javaScriptEnabled
        style={{ flex: 1, backgroundColor: colors.bg }}
        androidLayerType="hardware"
      />
    </View>
  );
}
