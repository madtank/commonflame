import { render, screen, userEvent } from "@/test/utils";
import { describe, expect, it, vi } from "vitest";
import { ComposerAgentRail } from "./ComposerAgentRail";

const AGENTS = [
  {
    handle: "nyx",
    name: "Nyx",
    emoji: "🦉",
    selected: true,
    availability: "online" as const,
  },
  {
    handle: "canary",
    name: "Canary",
    emoji: "🐤",
    selected: false,
    availability: "online" as const,
  },
];

function renderRail(
  overrides: Partial<Parameters<typeof ComposerAgentRail>[0]> = {},
) {
  const props = {
    agents: AGENTS,
    pinned: true,
    onToggleAgent: vi.fn(),
    onTogglePinned: vi.fn(),
    isDarkMode: false,
    ...overrides,
  };
  render(<ComposerAgentRail {...props} />);
  return props;
}

describe("ComposerAgentRail", () => {
  it("renders a toggle chip per agent with emoji and selection state", () => {
    renderRail();
    const nyx = screen.getByTestId("ax-rail-chip-nyx");
    expect(nyx).toHaveTextContent("🦉");
    expect(nyx).toHaveTextContent("@nyx");
    expect(nyx).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("ax-rail-chip-canary")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("toggles an agent on click instead of switching", async () => {
    const props = renderRail();
    await userEvent.click(screen.getByTestId("ax-rail-chip-canary"));
    expect(props.onToggleAgent).toHaveBeenCalledWith("canary");
    await userEvent.click(screen.getByTestId("ax-rail-chip-nyx"));
    expect(props.onToggleAgent).toHaveBeenCalledWith("nyx");
  });

  it("offers an unpin control while pinned", async () => {
    const props = renderRail();
    await userEvent.click(
      screen.getByRole("button", { name: "Unpin agent selector" }),
    );
    expect(props.onTogglePinned).toHaveBeenCalled();
  });

  it("renders nothing when unpinned", () => {
    renderRail({ pinned: false });
    expect(screen.queryByTestId("ax-agent-rail")).toBeNull();
  });
});
