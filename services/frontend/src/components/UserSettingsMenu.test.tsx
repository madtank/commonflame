import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen, userEvent } from "@/test/utils";

import { UserSettingsMenu } from "./UserSettingsMenu";

const { updateSettingsMock, useUserSettingsMock } = vi.hoisted(() => ({
  updateSettingsMock: vi.fn(async () => undefined),
  useUserSettingsMock: vi.fn(),
}));

vi.mock("@/hooks/useFeatureFlags", () => ({
  useFeatureFlags: () => ({
    flags: {},
    loaded: true,
    setFlag: vi.fn(async () => undefined),
  }),
}));

vi.mock("@/hooks/useUserSettings", () => ({
  useUserSettings: () => useUserSettingsMock(),
}));

vi.mock("@/lib/theme", async () => {
  const actual = await vi.importActual<typeof import("@/lib/theme")>(
    "@/lib/theme",
  );
  return {
    ...actual,
    THEME_TOGGLE_ENABLED: true,
  };
});

vi.mock("@/lib/storage", () => ({
  storage: {
    getUserMetadata: vi.fn(() => ({ feature_flags: {} })),
    setUserMetadata: vi.fn(),
  },
}));

vi.mock("@/lib/api-clean", () => ({
  updateUserFeatureFlags: vi.fn(async () => undefined),
}));

describe("UserSettingsMenu theme toggle", () => {
  beforeEach(() => {
    updateSettingsMock.mockClear();
    useUserSettingsMock.mockReturnValue({
      settings: { theme: "dark" },
      saving: false,
      updateSettings: updateSettingsMock,
    });
  });

  it("switches from dark mode to light mode", async () => {
    const user = userEvent.setup();
    render(<UserSettingsMenu onLogout={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /user settings/i }));
    await user.click(screen.getByRole("menuitem", { name: /dark mode/i }));

    expect(updateSettingsMock).toHaveBeenCalledWith({ theme: "light" });
  });

  it("switches from light mode to dark mode", async () => {
    const user = userEvent.setup();
    useUserSettingsMock.mockReturnValue({
      settings: { theme: "light" },
      saving: false,
      updateSettings: updateSettingsMock,
    });

    render(<UserSettingsMenu onLogout={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /user settings/i }));
    await user.click(screen.getByRole("menuitem", { name: /dark mode/i }));

    expect(updateSettingsMock).toHaveBeenCalledWith({ theme: "dark" });
  });

  it("treats system theme users as light when the OS prefers light", async () => {
    const user = userEvent.setup();
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as typeof window.matchMedia;
    useUserSettingsMock.mockReturnValue({
      settings: { theme: "system" },
      saving: false,
      updateSettings: updateSettingsMock,
    });

    render(<UserSettingsMenu onLogout={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /user settings/i }));
    await user.click(screen.getByRole("menuitem", { name: /dark mode/i }));

    expect(updateSettingsMock).toHaveBeenCalledWith({ theme: "dark" });
  });
});
