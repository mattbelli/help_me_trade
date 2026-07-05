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
