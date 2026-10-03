import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor } from "@/test/utils";
import { AxQuickMenu } from "./AxQuickMenu";
import { THEME_CHANGE_EVENT } from "@/lib/theme";

const updateSettings = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/useUserSettings", () => ({
  useUserSettings: () => ({
    settings: {
      ai_auto_summarize: true,
      theme: "dark",
    },
    updateSettings,
  }),
}));

describe("AxQuickMenu", () => {
  beforeEach(() => {
    updateSettings.mockClear();
    localStorage.clear();
    localStorage.setItem("theme", "dark");
    localStorage.setItem("darkMode", "true");
    document.documentElement.classList.add("dark");
    document.documentElement.dataset.theme = "dark";
  });

  function openMenu() {
    const onToggleCards = vi.fn();
    render(
      <AxQuickMenu
        username="Tester"
        email="tester@example.com"
        spaceName="Launchpad"
        agentCount={3}
        cardsEnabled={true}
        onToggleCards={onToggleCards}
        onOpenSettings={vi.fn()}
        onLogout={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
    return { onToggleCards };
  }

  it("hides the summary cards toggle by default", () => {
    openMenu();

    expect(
      screen.queryByRole("switch", { name: /toggle summary cards/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the summary cards toggle when explicitly enabled", () => {
    render(
      <AxQuickMenu
        username="Tester"
        email="tester@example.com"
        spaceName="Launchpad"
        agentCount={3}
        cardsEnabled={true}
        onToggleCards={vi.fn()}
        onOpenSettings={vi.fn()}
        onLogout={vi.fn()}
        showSummaryCardsToggle
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));

    expect(
      screen.getByRole("switch", { name: /toggle summary cards/i }),
    ).toBeInTheDocument();
  });

  it("shows a dark mode toggle and updates theme preference", async () => {
    openMenu();

    const darkModeSwitch = screen.getByRole("switch", {
      name: /toggle dark mode/i,
    });

    expect(darkModeSwitch).toHaveAttribute("aria-checked", "true");

    fireEvent.click(darkModeSwitch);

    await waitFor(() => {
      expect(updateSettings).toHaveBeenCalledWith({ theme: "light" });
    });
  });

  it("keeps auto summarize visible while hiding summary cards by default", () => {
    const { onToggleCards } = openMenu();

    expect(
      screen.getByRole("switch", { name: /toggle auto summarize/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("switch", { name: /toggle summary cards/i }),
    ).not.toBeInTheDocument();
    expect(onToggleCards).not.toHaveBeenCalled();
  });

  it("stays in sync with theme change events", async () => {
    openMenu();

    const darkModeSwitch = screen.getByRole("switch", {
      name: /toggle dark mode/i,
    });

    act(() => {
      window.dispatchEvent(
        new CustomEvent(THEME_CHANGE_EVENT, {
          detail: { theme: "light", isDarkMode: false },
        }),
      );
    });

    await waitFor(() => {
      expect(darkModeSwitch).toHaveAttribute("aria-checked", "false");
    });
  });
});
