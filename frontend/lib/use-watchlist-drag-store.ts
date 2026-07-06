"use client";

import { create } from "zustand";

// Bridges the watchlist widget (where a row is held/dragged) to its header
// (in workspace-grid.tsx) so the header can show a trash drop-target while a
// drag is in progress. `draggingWidget` is the dragging widget's instance id.
type WatchlistDragState = {
  draggingWidget: string | null;
  overTrash: boolean;
  trashRect: DOMRect | null;
  setDragging: (widget: string | null) => void;
  setOverTrash: (over: boolean) => void;
  setTrashRect: (rect: DOMRect | null) => void;
};

export const useWatchlistDragStore = create<WatchlistDragState>((set) => ({
  draggingWidget: null,
  overTrash: false,
  trashRect: null,
  setDragging: (widget) =>
    set(
      widget === null
        ? { draggingWidget: null, overTrash: false, trashRect: null }
        : { draggingWidget: widget },
    ),
  setOverTrash: (over) => set({ overTrash: over }),
  setTrashRect: (rect) => set({ trashRect: rect }),
}));
