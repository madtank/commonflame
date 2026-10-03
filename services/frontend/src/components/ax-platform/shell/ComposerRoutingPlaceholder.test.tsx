import { render, screen } from "@/test/utils";
import { describe, expect, it } from "vitest";
import { ComposerRoutingPlaceholder } from "./ComposerRoutingPlaceholder";

describe("ComposerRoutingPlaceholder", () => {
  it("highlights the routed agent emoji and handle inside the faded placeholder", () => {
    render(
      <ComposerRoutingPlaceholder
        routedAgents={[{ handle: "nyx", emoji: "🦉" }]}
        conciergeName="aX"
        isDarkMode={false}
      />,
    );

    const overlay = screen.getByTestId("ax-composer-placeholder");
    expect(overlay).toHaveTextContent("Message");
    const highlight = screen.getByTestId("ax-composer-placeholder-agent");
    expect(highlight).toHaveTextContent("🦉");
    expect(highlight).toHaveTextContent("@nyx");
  });

  it("invites a message to the space when no agent is routed", () => {
    render(
      <ComposerRoutingPlaceholder
        routedAgents={[]}
        conciergeName="aX"
        isDarkMode={false}
      />,
    );

    const overlay = screen.getByTestId("ax-composer-placeholder");
    expect(overlay).toHaveTextContent("Write to");
    expect(
      screen.getByTestId("ax-composer-placeholder-agent"),
    ).toHaveTextContent("this space");
  });

  it("never intercepts pointer events meant for the textarea", () => {
    render(
      <ComposerRoutingPlaceholder
        routedAgents={[{ handle: "nyx", emoji: "🦉" }]}
        conciergeName="aX"
        isDarkMode
      />,
    );

    expect(screen.getByTestId("ax-composer-placeholder").className).toContain(
      "pointer-events-none",
    );
  });

  it("lists up to three routed agents with their emojis", () => {
    render(
      <ComposerRoutingPlaceholder
        routedAgents={[
          { handle: "nyx", emoji: "🦉" },
          { handle: "peach", emoji: "🍑" },
        ]}
        conciergeName="aX"
        isDarkMode={false}
      />,
    );

    const highlight = screen.getByTestId("ax-composer-placeholder-agent");
    expect(highlight).toHaveTextContent("🦉");
    expect(highlight).toHaveTextContent("@nyx");
    expect(highlight).toHaveTextContent("🍑");
    expect(highlight).toHaveTextContent("@peach");
  });

  it("summarizes four or more routed agents as a count", () => {
    render(
      <ComposerRoutingPlaceholder
        routedAgents={[
          { handle: "a", emoji: "1" },
          { handle: "b", emoji: "2" },
          { handle: "c", emoji: "3" },
          { handle: "d", emoji: "4" },
        ]}
        conciergeName="aX"
        isDarkMode={false}
      />,
    );

    expect(
      screen.getByTestId("ax-composer-placeholder-agent"),
    ).toHaveTextContent("4 agents");
  });
});
