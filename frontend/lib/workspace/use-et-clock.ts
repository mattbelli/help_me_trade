"use client";

import { useEffect, useState } from "react";

function formatEtClock(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function useEtClock() {
  const [clock, setClock] = useState("—:—");

  useEffect(() => {
    const tick = () => setClock(formatEtClock(new Date()));
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);

  return `${clock} ET`;
}
