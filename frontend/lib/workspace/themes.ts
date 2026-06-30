export const WORKSPACE_THEMES = ["carbon", "warm", "oled", "light"] as const;

export type WorkspaceTheme = (typeof WORKSPACE_THEMES)[number];

export const WORKSPACE_THEME_LABELS: Record<WorkspaceTheme, string> = {
  carbon: "Carbon",
  warm: "Warm",
  oled: "OLED",
  light: "Light",
};
