/** Target pixel width per grid column — widgets keep ~this width across resolutions. */
export const GRID_COL_TARGET_PX = 56;

export const GRID_MARGIN_PX = 12;

export const GRID_MIN_COLS = 12;

/** Kept for any external callers that still reference it; harmless. */
export const GRID_MAX_COLS = 48;

/** Default before the grid container is measured. */
export const GRID_DEFAULT_COLS = 24;

/**
 * How many target-width columns fill the given container width.
 * Never returns less than GRID_MIN_COLS.
 */
export function minColsForWidth(containerWidth: number): number {
  const raw = Math.floor(
    (containerWidth + GRID_MARGIN_PX) /
      (GRID_COL_TARGET_PX + GRID_MARGIN_PX),
  );
  return Math.max(GRID_MIN_COLS, raw);
}

/**
 * Buffer columns kept to the right of all widgets so the user always has
 * empty space to drag into (infinite-canvas feel).
 */
export const RIGHT_BUFFER_COLS = 8;

/** Hard cap: prevents runaway column counts if a widget has a wild x+w value. */
export const MAX_COLS = 240;

/**
 * Compute the total column count for the growing canvas:
 *   - always fills the viewport (minColsForWidth)
 *   - always extends past the rightmost widget edge by RIGHT_BUFFER_COLS
 *   - clamped to [GRID_MIN_COLS, MAX_COLS]
 *
 * This is the replacement for deriveGridCols. The key difference: col count
 * GROWS with content but never artificially shrinks to the window width — so a
 * layout authored at 28 cols renders identically on any screen width.
 */
export function colsForLayout(
  layout: { x: number; w: number }[],
  containerWidth: number,
): number {
  const maxRightEdge = layout.reduce(
    (max, item) => Math.max(max, item.x + item.w),
    0,
  );
  const contentCols = maxRightEdge + RIGHT_BUFFER_COLS;
  const viewportCols = minColsForWidth(containerWidth);
  return Math.min(MAX_COLS, Math.max(viewportCols, contentCols, GRID_MIN_COLS));
}

export function gridPixelWidth(cols: number): number {
  return cols * GRID_COL_TARGET_PX + (cols - 1) * GRID_MARGIN_PX;
}

/**
 * Kept for backward compatibility — previously used to clamp widget x+w into
 * the visible column range. The growing-canvas model no longer squashes widgets,
 * so call sites should not use this. Exported so lingering imports don't break.
 */
export function clampLayoutWidth(
  x: number,
  w: number,
  cols: number,
): { x: number; w: number } {
  const clampedW = Math.min(w, cols);
  const clampedX = Math.min(x, Math.max(0, cols - clampedW));
  return { x: clampedX, w: clampedW };
}

/**
 * @deprecated Use colsForLayout instead. Kept so any import of deriveGridCols
 * doesn't break until callers are updated.
 */
export function deriveGridCols(containerWidth: number): number {
  return minColsForWidth(containerWidth);
}
