/** Cross-page signal that persisted settings (especially AI keys) changed. */

export const SETTINGS_SAVED_EVENT = "hmt:settings-saved";

export function dispatchSettingsSaved(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(SETTINGS_SAVED_EVENT));
}

export function subscribeSettingsSaved(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(SETTINGS_SAVED_EVENT, listener);
  return () => window.removeEventListener(SETTINGS_SAVED_EVENT, listener);
}
