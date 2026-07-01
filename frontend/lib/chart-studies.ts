import { emaSeries, smaSeries } from "./indicators";

export type ChartStudyId = "sma20" | "sma50" | "sma200" | "ema20";

export type OverlayStudyDef = {
  id: ChartStudyId;
  label: string;
  color: string;
  compute: (closes: number[]) => (number | null)[];
};

export const OVERLAY_STUDIES: OverlayStudyDef[] = [
  { id: "sma20", label: "SMA 20", color: "#e0b040", compute: (c) => smaSeries(c, 20) },
  { id: "sma50", label: "SMA 50", color: "#4d9de0", compute: (c) => smaSeries(c, 50) },
  { id: "sma200", label: "SMA 200", color: "#9d6ce0", compute: (c) => smaSeries(c, 200) },
  { id: "ema20", label: "EMA 20", color: "#3fb8a0", compute: (c) => emaSeries(c, 20) },
];

export type OscillatorStudyId = "rsi" | "macd";
export type OscillatorStudyDef = { id: OscillatorStudyId; label: string };
export const OSCILLATOR_STUDIES: OscillatorStudyDef[] = [
  { id: "rsi", label: "RSI 14" },
  { id: "macd", label: "MACD" },
];
