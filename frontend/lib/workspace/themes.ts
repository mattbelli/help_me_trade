export const WORKSPACE_THEMES = ["carbon", "lab", "warm", "oled", "light"] as const;

export type WorkspaceTheme = (typeof WORKSPACE_THEMES)[number];

/** A built-in theme id or a custom one ("custom-…"). */
export type ThemeId = WorkspaceTheme | string;

export const WORKSPACE_THEME_LABELS: Record<WorkspaceTheme, string> = {
  carbon: "Carbon",
  lab: "Lab",
  warm: "Ember",
  oled: "OLED",
  light: "Light",
};

export function isBuiltinTheme(id: string | undefined | null): id is WorkspaceTheme {
  return !!id && (WORKSPACE_THEMES as readonly string[]).includes(id);
}
