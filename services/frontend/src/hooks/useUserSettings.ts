import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-clean";
import { safeLocalStorage } from "@/lib/safe-local-storage";
import {
  applyThemePreference,
  getStoredThemePreference,
  THEME_STORAGE_KEY,
  type ThemePreference,
} from "@/lib/theme";

export interface UserSettings {
  email_notifications: boolean;
  mention_notifications: boolean;
  task_notifications: boolean;
  ai_suggestions_enabled: boolean;
  ai_auto_summarize: boolean;
  theme: ThemePreference;
  compact_mode: boolean;
  custom: Record<string, unknown>;
}

const SETTINGS_STORAGE_KEY = "ax:user-settings";
const LOGIN_THEME_STORAGE_KEY = "ax:login-theme-preference";
const USER_SETTINGS_QUERY_KEY = ["user-settings"] as const;
export const USER_SETTINGS_CHANGED_EVENT = "ax:user-settings-changed";

function isThemePreference(value: string | null): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

function createDefaultSettings(): UserSettings {
  return {
    email_notifications: true,
    mention_notifications: true,
    task_notifications: true,
    ai_suggestions_enabled: false,
    ai_auto_summarize: true,
    theme: getStoredThemePreference(),
    compact_mode: false,
    custom: {},
  };
}

function normalizeSettings(value?: Partial<UserSettings> | null): UserSettings {
  return {
    ...createDefaultSettings(),
    ...(value || {}),
    custom:
      value?.custom && typeof value.custom === "object" ? value.custom : {},
    theme: isThemePreference(value?.theme ?? null)
      ? value.theme
      : getStoredThemePreference(),
  };
}

export function getStoredUserSettings(): UserSettings {
  const raw = safeLocalStorage.getItem(SETTINGS_STORAGE_KEY);
  if (!raw) return createDefaultSettings();

  try {
    const parsed = normalizeSettings(JSON.parse(raw) as Partial<UserSettings>);
    return {
      ...parsed,
      theme: parsed.theme || getStoredThemePreference(),
    };
  } catch {
    return createDefaultSettings();
  }
}

export function persistStoredUserSettings(
  next: UserSettings,
  options?: { emitEvent?: boolean },
): void {
  safeLocalStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(next));
  safeLocalStorage.setItem(THEME_STORAGE_KEY, next.theme);
  applyThemePreference(next.theme, { emitEvent: options?.emitEvent });
  if (options?.emitEvent !== false && typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent<UserSettings>(USER_SETTINGS_CHANGED_EVENT, {
        detail: next,
      }),
    );
  }
}

export function persistThemePreferenceForUserSettings(
  theme: ThemePreference,
  options?: { emitEvent?: boolean },
): UserSettings {
  const next = normalizeSettings({
    ...getStoredUserSettings(),
    theme,
  });
  persistStoredUserSettings(next, options);
  return next;
}

export function persistLoginThemePreferenceForUserSettings(
  theme: ThemePreference,
  options?: { emitEvent?: boolean },
): UserSettings {
  safeLocalStorage.setItem(LOGIN_THEME_STORAGE_KEY, theme);
  return persistThemePreferenceForUserSettings(theme, options);
}

function getPendingLoginThemePreference(): ThemePreference | null {
  const theme = safeLocalStorage.getItem(LOGIN_THEME_STORAGE_KEY);
  return isThemePreference(theme) ? theme : null;
}

export function clearPendingLoginThemePreference(): void {
  safeLocalStorage.removeItem(LOGIN_THEME_STORAGE_KEY);
}

function getStoredUserSettingsWithPendingLoginTheme(): UserSettings {
  const storedSettings = getStoredUserSettings();
  const pendingLoginTheme = getPendingLoginThemePreference();

  return pendingLoginTheme
    ? normalizeSettings({ ...storedSettings, theme: pendingLoginTheme })
    : storedSettings;
}

async function syncLoginThemePreferenceToServer(
  loginTheme: ThemePreference,
): Promise<void> {
  if (getPendingLoginThemePreference() !== loginTheme) return;

  try {
    await apiClient.patch("/api/v1/settings", { theme: loginTheme });

    const currentTheme = getStoredUserSettings().theme;
    if (currentTheme !== loginTheme) {
      await apiClient.patch("/api/v1/settings", { theme: currentTheme });
      return;
    }

    if (getPendingLoginThemePreference() === loginTheme) {
      clearPendingLoginThemePreference();
    }
  } catch (err) {
    console.warn(
      "Failed to sync login theme preference to user settings:",
      err,
    );
  }
}

function getErrorStatus(error: unknown): number | null {
  if (!error || typeof error !== "object" || !("response" in error)) {
    return null;
  }

  const response = (error as { response?: { status?: unknown } }).response;
  return typeof response?.status === "number" ? response.status : null;
}

// Track whether the settings endpoint is available (avoids repeated 404s)
let endpointAvailable: boolean | null = null;

async function fetchUserSettings(): Promise<UserSettings> {
  const storedSettings = getStoredUserSettingsWithPendingLoginTheme();
  const pendingLoginTheme = getPendingLoginThemePreference();

  if (endpointAvailable === false) {
    return storedSettings;
  }

  try {
    const response = await apiClient.get("/api/v1/settings");
    endpointAvailable = true;
    const data = normalizeSettings(response.data as UserSettings);
    const settings = pendingLoginTheme
      ? normalizeSettings({ ...data, theme: pendingLoginTheme })
      : data;
    persistStoredUserSettings(settings);

    if (pendingLoginTheme) {
      if (data.theme === pendingLoginTheme) {
        clearPendingLoginThemePreference();
      } else {
        void syncLoginThemePreferenceToServer(pendingLoginTheme);
      }
    }

    return settings;
  } catch (err) {
    if (getErrorStatus(err) === 404) {
      endpointAvailable = false;
      return storedSettings;
    }
    throw err;
  }
}

export function useUserSettings() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const settingsQuery = useQuery({
    queryKey: USER_SETTINGS_QUERY_KEY,
    queryFn: fetchUserSettings,
    initialData: getStoredUserSettingsWithPendingLoginTheme,
    initialDataUpdatedAt: 0,
    staleTime: 5 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (settingsQuery.data) {
      applyThemePreference(settingsQuery.data.theme);
    }
  }, [settingsQuery.data]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const handleSettingsChanged = (event: Event) => {
      const next = normalizeSettings(
        (event as CustomEvent<UserSettings>).detail,
      );
      queryClient.setQueryData(USER_SETTINGS_QUERY_KEY, next);
    };

    window.addEventListener(USER_SETTINGS_CHANGED_EVENT, handleSettingsChanged);

    return () =>
      window.removeEventListener(
        USER_SETTINGS_CHANGED_EVENT,
        handleSettingsChanged,
      );
  }, [queryClient]);

  const updateMutation = useMutation({
    mutationFn: async (patch: Partial<UserSettings>) => {
      if (endpointAvailable === false) {
        const current =
          queryClient.getQueryData<UserSettings>(USER_SETTINGS_QUERY_KEY) ||
          getStoredUserSettings();
        return normalizeSettings({ ...current, ...patch });
      }

      const response = await apiClient.patch("/api/v1/settings", patch);
      return normalizeSettings(response.data as UserSettings);
    },
    onMutate: async (patch) => {
      await queryClient.cancelQueries({ queryKey: USER_SETTINGS_QUERY_KEY });
      setError(null);

      const previous =
        queryClient.getQueryData<UserSettings>(USER_SETTINGS_QUERY_KEY) ||
        getStoredUserSettingsWithPendingLoginTheme();
      const next = normalizeSettings({ ...previous, ...patch });

      if (isThemePreference(patch.theme ?? null)) {
        clearPendingLoginThemePreference();
      }

      queryClient.setQueryData(USER_SETTINGS_QUERY_KEY, next);
      persistStoredUserSettings(next);

      return { previous };
    },
    onSuccess: (data) => {
      queryClient.setQueryData(USER_SETTINGS_QUERY_KEY, data);
      persistStoredUserSettings(data);
    },
    onError: (err, _patch, context) => {
      if (getErrorStatus(err) === 404) {
        endpointAvailable = false;
        return;
      }

      const previous = context?.previous || getStoredUserSettings();
      queryClient.setQueryData(USER_SETTINGS_QUERY_KEY, previous);
      persistStoredUserSettings(previous);
      setError("Failed to save settings");
    },
  });

  const updateSettings = async (patch: Partial<UserSettings>) => {
    try {
      await updateMutation.mutateAsync(patch);
    } catch (err) {
      if (getErrorStatus(err) === 404) return;
      console.error("Failed to update settings:", err);
      throw err;
    }
  };

  const settings = useMemo(
    () => normalizeSettings(settingsQuery.data),
    [settingsQuery.data],
  );

  return {
    settings,
    loading: settingsQuery.isPending,
    saving: updateMutation.isPending,
    error:
      error || (settingsQuery.error ? "Failed to load settings" : null) || null,
    updateSettings,
    refetch: async () => {
      setError(null);
      try {
        const result = await settingsQuery.refetch();
        if (result.error) {
          console.error("Failed to fetch user settings:", result.error);
          setError("Failed to load settings");
        }
      } catch (err) {
        console.error("Failed to fetch user settings:", err);
        setError("Failed to load settings");
      }
    },
  };
}
