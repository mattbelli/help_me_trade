"use client";

import { create } from "zustand";

/**
 * The active manual-order quantity, shared across trading surfaces: the
 * ticket and DOM publish their qty here, and the chart's context-menu order
 * items read it (the chart has no qty field of its own — platform convention:
 * quantity is set before the click, wherever it was last set).
 */
type ManualQtyState = {
  qty: string;
  setQty: (qty: string) => void;
};

export const useManualQtyStore = create<ManualQtyState>((set) => ({
  qty: "1",
  setQty: (qty) => set({ qty }),
}));
