/**
 * Tests for theme system.
 *
 * Verifies that light/dark/system preferences resolve correctly
 * and that applyThemePreference still dispatches theme change events.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  applyThemePreference,
  resolveDarkMode,
  THEME_CHANGE_EVENT,
  THEME_TOGGLE_ENABLED,
} from "./theme";

describe("theme toggle", () => {
  it("is enabled", () => {
    expect(THEME_TOGGLE_ENABLED).toBe(true);
  });
});

describe("resolveDarkMode", () => {
  const originalMatchMedia = window.matchMedia;

  afterEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: originalMatchMedia,
    });
  });

  it("returns true for 'dark' preference", () => {
    expect(resolveDarkMode("dark")).toBe(true);
  });

  it("returns false for 'light' preference", () => {
    expect(resolveDarkMode("light")).toBe(false);
  });

  it("respects the system preference", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: matchMedia,
    });
    expect(resolveDarkMode("system")).toBe(true);
  });
});

describe("applyThemePreference", () => {
  const originalMatchMedia = window.matchMedia;

  afterEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: originalMatchMedia,
    });
  });

  beforeEach(() => {
    document.documentElement.classList.remove("dark");
    delete document.documentElement.dataset.theme;
  });

  it("adds 'dark' class for dark theme", () => {
    const result = applyThemePreference("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(result.isDarkMode).toBe(true);
  });

  it("removes the 'dark' class for light theme", () => {
    document.documentElement.classList.add("dark");
    const result = applyThemePreference("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(result.isDarkMode).toBe(false);
  });

  it("applies the resolved system theme", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: false });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: matchMedia,
    });
    const result = applyThemePreference("system");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(result.isDarkMode).toBe(false);
  });

  it("dispatches theme change event", () => {
    const handler = vi.fn();
    window.addEventListener(THEME_CHANGE_EVENT, handler);
    applyThemePreference("dark");
    expect(handler).toHaveBeenCalledOnce();
    window.removeEventListener(THEME_CHANGE_EVENT, handler);
  });
});
