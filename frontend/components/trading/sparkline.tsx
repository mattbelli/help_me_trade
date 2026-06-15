"use client";

import { cn } from "@/lib/utils";

type Props = {
  values: number[];
  width?: number;
  height?: number;
  className?: string;
};

export function Sparkline({
  values,
  width = 56,
  height = 22,
  className,
}: Props) {
  if (values.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        className={cn("text-muted-foreground/40", className)}
        aria-hidden
      >
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="currentColor"
          strokeWidth={1}
          strokeDasharray="2 2"
        />
      </svg>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 1.5;

  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y =
      height - pad - ((value - min) / range) * (height - pad * 2);
    return `${x},${y}`;
  });

  const up = values[values.length - 1] >= values[0];
  const strokeClass = up ? "text-pnl-positive" : "text-pnl-negative";

  return (
    <svg
      width={width}
      height={height}
      className={cn(strokeClass, className)}
      aria-hidden
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points.join(" ")}
      />
    </svg>
  );
}
