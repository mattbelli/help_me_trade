export const WORKSPACE_THEMES = ["carbon", "ember", "paper"] as const;

export type WorkspaceTheme = (typeof WORKSPACE_THEMES)[number];

export const WORKSPACE_THEME_LABELS: Record<WorkspaceTheme, string> = {
  carbon: "Carbon",
  ember: "Ember",
  paper: "Paper",
};
