"use client"

import * as React from "react"
import { ThemeProvider as NextThemesProvider, useTheme } from "next-themes"

import { WORKSPACE_THEMES } from "@/lib/workspace/themes"
import {
  injectCustomThemeStyles,
  resolveThemeIsDark,
  syncHtmlDarkClass,
} from "@/lib/workspace/custom-themes"
import { useCustomThemeStore } from "@/lib/workspace/use-custom-theme-store"
import {
  DEFAULT_DARK_THEME,
  DEFAULT_LIGHT_THEME,
  useThemeModeStore,
} from "@/lib/workspace/use-theme-mode-store"

const THEME_STORAGE_KEY = "hmt-theme-carbon"

const LEGACY_THEME_ALIASES: Record<string, string> = {
  paper: "light",
  ember: "warm",
  dark: "carbon",
}

function migrateLegacyThemePreference() {
  if (typeof window === "undefined") {
    return
  }

  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (stored && stored in LEGACY_THEME_ALIASES) {
      window.localStorage.setItem(THEME_STORAGE_KEY, LEGACY_THEME_ALIASES[stored])
    }
  } catch {
    // ignore storage errors
  }
}

if (typeof window !== "undefined") {
  migrateLegacyThemePreference()
}

function ThemeProvider({
  children,
  ...props
}: React.ComponentProps<typeof NextThemesProvider>) {
  const customThemes = useCustomThemeStore((s) => s.themes)
  const hydrate = useCustomThemeStore((s) => s.hydrate)

  // Reconcile custom themes from server + (re)inject Layer-A styles on mount so
  // the React side agrees with the anti-FOUC head script.
  React.useEffect(() => {
    void hydrate()
  }, [hydrate])

  React.useEffect(() => {
    injectCustomThemeStyles(customThemes)
  }, [customThemes])

  const themes = React.useMemo(
    () => [...WORKSPACE_THEMES, ...customThemes.map((t) => t.id)],
    [customThemes],
  )

  return (
    <NextThemesProvider
      attribute="data-theme"
      defaultTheme="carbon"
      themes={themes}
      storageKey={THEME_STORAGE_KEY}
      disableTransitionOnChange
      enableSystem={false}
      enableColorScheme={false}
      {...props}
    >
      <ThemeMigration />
      <ThemeDarkClass />
      <ThemeSystemSync />
      <ThemeHotkey />
      {children}
    </NextThemesProvider>
  )
}

function ThemeMigration() {
  const { theme, setTheme } = useTheme()

  React.useEffect(() => {
    if (theme && theme in LEGACY_THEME_ALIASES) {
      setTheme(LEGACY_THEME_ALIASES[theme])
    }
  }, [theme, setTheme])

  return null
}

function ThemeDarkClass() {
  const { theme } = useTheme()
  const customThemes = useCustomThemeStore((s) => s.themes)

  React.useEffect(() => {
    syncHtmlDarkClass(resolveThemeIsDark(theme, customThemes))
  }, [theme, customThemes])

  return null
}

/** Resolve a stored default id, falling back to the built-in when it no longer
 *  exists (e.g. the custom theme it pointed at was deleted). */
function resolveDefault(
  id: string,
  fallback: string,
  customThemes: { id: string }[],
): string {
  const known =
    (WORKSPACE_THEMES as readonly string[]).includes(id) ||
    customThemes.some((t) => t.id === id)
  return known ? id : fallback
}

/** When "System" mode is active, follow the OS `prefers-color-scheme` and apply
 *  the user's default-dark or default-light theme accordingly. Lives here (in
 *  the always-mounted provider) so it keeps tracking the OS with the menu shut. */
function ThemeSystemSync() {
  const { setTheme } = useTheme()
  const customThemes = useCustomThemeStore((s) => s.themes)
  const system = useThemeModeStore((s) => s.system)
  const defaultDark = useThemeModeStore((s) => s.defaultDark)
  const defaultLight = useThemeModeStore((s) => s.defaultLight)

  React.useEffect(() => {
    if (!system || typeof window === "undefined" || !window.matchMedia) return
    const mq = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = () => {
      setTheme(
        mq.matches
          ? resolveDefault(defaultDark, DEFAULT_DARK_THEME, customThemes)
          : resolveDefault(defaultLight, DEFAULT_LIGHT_THEME, customThemes),
      )
    }
    apply()
    mq.addEventListener("change", apply)
    return () => mq.removeEventListener("change", apply)
  }, [system, defaultDark, defaultLight, customThemes, setTheme])

  return null
}

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  )
}

function ThemeHotkey() {
  const { resolvedTheme, setTheme } = useTheme()

  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) {
        return
      }

      if (event.metaKey || event.ctrlKey || event.altKey) {
        return
      }

      if (event.key.toLowerCase() !== "d") {
        return
      }

      if (isTypingTarget(event.target)) {
        return
      }

      setTheme(resolvedTheme === "light" ? "carbon" : "light")
    }

    window.addEventListener("keydown", onKeyDown)

    return () => {
      window.removeEventListener("keydown", onKeyDown)
    }
  }, [resolvedTheme, setTheme])

  return null
}

export { ThemeProvider }
