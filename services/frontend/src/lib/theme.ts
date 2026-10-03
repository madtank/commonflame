import { safeLocalStorage } from "@/lib/safe-local-storage";

export type ThemePreference = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "theme";
export const LEGACY_DARK_MODE_STORAGE_KEY = "darkMode";
export const THEME_CHANGE_EVENT = "ax:theme-changed";

/**
 * Theme toggle is enabled again so login and in-app surfaces can share the
 * same persisted preference.
 */
export const THEME_TOGGLE_ENABLED = true;

type ThemeChangeDetail = {
  theme: ThemePreference;
  isDarkMode: boolean;
};

function isThemePreference(value: string | null): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

export function getStoredThemePreference(): ThemePreference {
  const storedTheme = safeLocalStorage.getItem(THEME_STORAGE_KEY);
  if (isThemePreference(storedTheme)) {
    return storedTheme;
  }

  const legacyDarkMode = safeLocalStorage.getItem(LEGACY_DARK_MODE_STORAGE_KEY);
  if (legacyDarkMode === "true") return "dark";
  if (legacyDarkMode === "false") return "light";

  return "system";
}

export function resolveDarkMode(theme: ThemePreference): boolean {
  if (!THEME_TOGGLE_ENABLED) return true;

  if (theme === "dark") return true;
  if (theme === "light") return false;

  if (typeof window === "undefined" || !window.matchMedia) {
    return false;
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyThemePreference(
  theme: ThemePreference,
  options?: { emitEvent?: boolean },
): ThemeChangeDetail {
  const isDarkMode = resolveDarkMode(theme);

  safeLocalStorage.setItem(THEME_STORAGE_KEY, theme);
  safeLocalStorage.setItem(LEGACY_DARK_MODE_STORAGE_KEY, String(isDarkMode));

  if (typeof document !== "undefined") {
    document.documentElement.classList.toggle("dark", isDarkMode);
    document.documentElement.dataset.theme = isDarkMode ? "dark" : "light";
  }

  if (options?.emitEvent !== false && typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent<ThemeChangeDetail>(THEME_CHANGE_EVENT, {
        detail: { theme, isDarkMode },
      }),
    );
  }

  return { theme, isDarkMode };
}

export function getStoredThemeState(): ThemeChangeDetail {
  const theme = getStoredThemePreference();
  return {
    theme,
    isDarkMode: resolveDarkMode(theme),
  };
}
