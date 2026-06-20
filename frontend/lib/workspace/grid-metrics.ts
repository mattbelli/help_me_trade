/** Target pixel width per grid column — widgets keep ~this width across resolutions. */
export const GRID_COL_TARGET_PX = 56;

export const GRID_MARGIN_PX = 12;

export const GRID_MIN_COLS = 12;

export const GRID_MAX_COLS = 48;

/** Default before the grid container is measured. */
export const GRID_DEFAULT_COLS = 24;

export function deriveGridCols(containerWidth: number): number {
  const raw = Math.floor(
    (containerWidth + GRID_MARGIN_PX) /
      (GRID_COL_TARGET_PX + GRID_MARGIN_PX),
  );
  // Floor at the default column count so a narrowed desktop window keeps the
  // authored layout intact (no clamping widgets on top of each other). Phones
  // get the dedicated mobile shell, so the grid never needs to go below this.
  return Math.min(GRID_MAX_COLS, Math.max(GRID_DEFAULT_COLS, raw));
}

export function gridPixelWidth(cols: number): number {
  return cols * GRID_COL_TARGET_PX + (cols - 1) * GRID_MARGIN_PX;
}

export function clampLayoutWidth(
  x: number,
  w: number,
  cols: number,
): { x: number; w: number } {
  const clampedW = Math.min(w, cols);
  const clampedX = Math.min(x, Math.max(0, cols - clampedW));
  return { x: clampedX, w: clampedW };
}
