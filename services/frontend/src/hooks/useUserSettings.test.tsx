import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  getStoredUserSettings,
  persistLoginThemePreferenceForUserSettings,
  useUserSettings,
} from "@/hooks/useUserSettings";

const { getMock, patchMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  patchMock: vi.fn(),
}));

vi.mock("@/lib/api-clean", () => ({
  apiClient: {
    get: getMock,
    patch: patchMock,
  },
}));

const { getItemMock, setItemMock, removeItemMock } = vi.hoisted(() => ({
  getItemMock: vi.fn<(key: string) => string | null>(() => null),
  setItemMock: vi.fn<(key: string, value: string) => void>(),
  removeItemMock: vi.fn<(key: string) => void>(),
}));

vi.mock("@/lib/safe-local-storage", () => ({
  safeLocalStorage: {
    getItem: getItemMock,
    setItem: setItemMock,
    removeItem: removeItemMock,
  },
}));

vi.mock("@/lib/theme", () => ({
  applyThemePreference: vi.fn(),
  getStoredThemePreference: vi.fn(() => "system"),
  THEME_STORAGE_KEY: "theme",
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

function DualConsumer() {
  const first = useUserSettings();
  const second = useUserSettings();

  return (
    <div>
      <span data-testid="first-theme">{first.settings.theme}</span>
      <span data-testid="second-theme">{second.settings.theme}</span>
    </div>
  );
}

function ThemeToggleConsumer() {
  const { settings, updateSettings } = useUserSettings();

  return (
    <div>
      <span data-testid="active-theme">{settings.theme}</span>
      <button onClick={() => void updateSettings({ theme: "dark" })}>
        Use dark
      </button>
    </div>
  );
}

describe("useUserSettings", () => {
  beforeEach(() => {
    getMock.mockReset();
    patchMock.mockReset();
    getItemMock.mockReset();
    setItemMock.mockReset();
    removeItemMock.mockReset();
    getItemMock.mockReturnValue(null);
  });

  it("shares a single settings request across multiple consumers", async () => {
    getMock.mockResolvedValue({
      data: {
        ai_auto_summarize: true,
        theme: "dark",
      },
    });

    render(<DualConsumer />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("first-theme")).toHaveTextContent("dark");
      expect(screen.getByTestId("second-theme")).toHaveTextContent("dark");
    });

    expect(getMock).toHaveBeenCalledTimes(1);
    expect(getMock).toHaveBeenCalledWith("/api/v1/settings");
  });

  it("defaults auto summarize to enabled in stored fallback settings", () => {
    expect(getStoredUserSettings().ai_auto_summarize).toBe(true);
  });

  it("persists a login theme choice into stored user settings", () => {
    const store = new Map<string, string>();
    getItemMock.mockImplementation((key: string) => store.get(key) ?? null);
    setItemMock.mockImplementation((key: string, value: string) => {
      store.set(key, value);
    });

    const next = persistLoginThemePreferenceForUserSettings("light", {
      emitEvent: false,
    });

    expect(next.theme).toBe("light");
    expect(getStoredUserSettings().theme).toBe("light");
    expect(store.get("theme")).toBe("light");
    expect(store.get("ax:login-theme-preference")).toBe("light");
    expect(JSON.parse(store.get("ax:user-settings") || "{}")).toMatchObject({
      theme: "light",
    });
  });

  it("keeps a login theme choice over stale server settings", async () => {
    const store = new Map<string, string>([
      ["theme", "light"],
      ["ax:login-theme-preference", "light"],
      [
        "ax:user-settings",
        JSON.stringify({
          email_notifications: true,
          mention_notifications: true,
          task_notifications: true,
          ai_suggestions_enabled: false,
          ai_auto_summarize: true,
          theme: "light",
          compact_mode: false,
          custom: {},
        }),
      ],
    ]);
    getItemMock.mockImplementation((key: string) => store.get(key) ?? null);
    setItemMock.mockImplementation((key: string, value: string) => {
      store.set(key, value);
    });
    removeItemMock.mockImplementation((key: string) => {
      store.delete(key);
    });
    getMock.mockResolvedValue({
      data: {
        ai_auto_summarize: true,
        theme: "dark",
      },
    });
    patchMock.mockResolvedValue({
      data: {
        ai_auto_summarize: true,
        theme: "light",
      },
    });

    render(<DualConsumer />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(getMock).toHaveBeenCalledWith("/api/v1/settings");
    });
    await waitFor(() => {
      expect(patchMock).toHaveBeenCalledWith("/api/v1/settings", {
        theme: "light",
      });
    });

    expect(screen.getByTestId("first-theme")).toHaveTextContent("light");
    expect(screen.getByTestId("second-theme")).toHaveTextContent("light");

    await waitFor(() => {
      expect(removeItemMock).toHaveBeenCalledWith("ax:login-theme-preference");
    });
  });

  it("uses a pending login theme over stale stored settings before the server returns", () => {
    const store = new Map<string, string>([
      ["theme", "light"],
      ["ax:login-theme-preference", "light"],
      [
        "ax:user-settings",
        JSON.stringify({
          email_notifications: true,
          mention_notifications: true,
          task_notifications: true,
          ai_suggestions_enabled: false,
          ai_auto_summarize: true,
          theme: "dark",
          compact_mode: false,
          custom: {},
        }),
      ],
    ]);
    getItemMock.mockImplementation((key: string) => store.get(key) ?? null);
    setItemMock.mockImplementation((key: string, value: string) => {
      store.set(key, value);
    });
    getMock.mockResolvedValue({
      data: {
        ai_auto_summarize: true,
        theme: "dark",
      },
    });

    render(<DualConsumer />, { wrapper: createWrapper() });

    expect(screen.getByTestId("first-theme")).toHaveTextContent("light");
    expect(screen.getByTestId("second-theme")).toHaveTextContent("light");
  });

  it("clears a pending login theme when the in-app theme toggle wins", async () => {
    const store = new Map<string, string>([
      ["theme", "light"],
      ["ax:login-theme-preference", "light"],
      [
        "ax:user-settings",
        JSON.stringify({
          email_notifications: true,
          mention_notifications: true,
          task_notifications: true,
          ai_suggestions_enabled: false,
          ai_auto_summarize: true,
          theme: "light",
          compact_mode: false,
          custom: {},
        }),
      ],
    ]);
    getItemMock.mockImplementation((key: string) => store.get(key) ?? null);
    setItemMock.mockImplementation((key: string, value: string) => {
      store.set(key, value);
    });
    removeItemMock.mockImplementation((key: string) => {
      store.delete(key);
    });
    getMock.mockResolvedValue({
      data: {
        ai_auto_summarize: true,
        theme: "light",
      },
    });
    patchMock.mockResolvedValue({
      data: {
        ai_auto_summarize: true,
        theme: "dark",
      },
    });

    render(<ThemeToggleConsumer />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByRole("button", { name: /use dark/i }));

    await waitFor(() => {
      expect(screen.getByTestId("active-theme")).toHaveTextContent("dark");
    });
    expect(removeItemMock).toHaveBeenCalledWith("ax:login-theme-preference");
    expect(patchMock).toHaveBeenCalledWith("/api/v1/settings", {
      theme: "dark",
    });
  });
});
