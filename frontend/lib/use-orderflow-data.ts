"use client";

import { useEffect, useState } from "react";

import {
  fetchOrderFlowBook,
  fetchOrderFlowCandles,
  fetchOrderFlowFootprint,
  fetchOrderFlowMeta,
  fetchOrderFlowTape,
  type BookData,
  type CandleData,
  type FootprintData,
  type OrderFlowMeta,
  type TapeData,
} from "@/lib/orderflow-api";

export type OrderFlowDataState = {
  meta: OrderFlowMeta | null;
  book: BookData | null;
  tape: TapeData | null;
  candles: CandleData | null;
  footprint: FootprintData | null;
  loading: boolean;
  error: string | null;
};

/**
 * Source-agnostic order-flow data hook.
 *
 * Current source: REST + replay (historical MBO parquets via FastAPI).
 *
 * Two extension points for the future:
 *
 * 1. **Live data source** — swap the five `fetchOrderFlow*` calls below for a
 *    single WebSocket subscription (or a streaming API) that emits the same
 *    `{ meta, book, tape, candles, footprint }` shape.  Because OrderFlowViewer
 *    and OrderFlowCanvas only see this hook's return value, no component changes
 *    are required.
 *
 * 2. **Widget callId prop** — OrderFlowViewer now accepts an optional `callId`
 *    prop that bypasses the URL ?call= param.  Pass it when mounting the viewer
 *    as a dashboard widget so the host component controls the selected call
 *    without polluting the browser history.
 */
export function useOrderFlowData(postId: string | null): OrderFlowDataState {
  const [state, setState] = useState<OrderFlowDataState>({
    meta: null,
    book: null,
    tape: null,
    candles: null,
    footprint: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (!postId) {
      setState({
        meta: null,
        book: null,
        tape: null,
        candles: null,
        footprint: null,
        loading: false,
        error: null,
      });
      return;
    }

    let cancelled = false;
    // Reset for the new call. `loading` covers the ESSENTIALS only (meta +
    // candles + footprint) so the chart renders as soon as they arrive — it is
    // NOT held hostage by the heavy book reconstruction.
    setState({
      meta: null, book: null, tape: null, candles: null, footprint: null,
      loading: true, error: null,
    });

    // ── Essentials: render the chart the moment these resolve ────────────────
    Promise.allSettled([
      fetchOrderFlowMeta(postId),
      fetchOrderFlowCandles(postId),
      fetchOrderFlowFootprint(postId),
    ]).then(([metaResult, candlesResult, footprintResult]) => {
      if (cancelled) return;
      const essentialFailure =
        metaResult.status === "rejected"
          ? metaResult.reason
          : candlesResult.status === "rejected"
            ? candlesResult.reason
            : footprintResult.status === "rejected"
              ? footprintResult.reason
              : null;
      setState((prev) => ({
        ...prev,
        meta:      metaResult.status      === "fulfilled" ? metaResult.value      : null,
        candles:   candlesResult.status   === "fulfilled" ? candlesResult.value   : null,
        footprint: footprintResult.status === "fulfilled" ? footprintResult.value : null,
        loading: false,
        error: essentialFailure instanceof Error
          ? essentialFailure.message
          : essentialFailure
            ? "Failed to load order flow data"
            : null,
      }));
    });

    // ── Optional + slow: book (heavy reconstruction) + tape merge in later ───
    //    without blocking the chart. The DOM/heatmap show their own loading
    //    state until the book arrives.
    fetchOrderFlowBook(postId)
      .then((book) => { if (!cancelled) setState((prev) => ({ ...prev, book })); })
      .catch(() => {});
    fetchOrderFlowTape(postId)
      .then((tape) => { if (!cancelled) setState((prev) => ({ ...prev, tape })); })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [postId]);

  return state;
}
