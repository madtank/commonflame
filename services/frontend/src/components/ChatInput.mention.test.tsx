import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, userEvent } from "@/test/utils";
import "@testing-library/jest-dom";

import { ChatInput } from "./ChatInput";
import { api } from "@/lib/api-clean";

vi.mock("@/lib/api-clean", () => ({
  api: {
    getTeams: vi.fn(async () => []),
    postMessage: vi.fn(async () => ({ status: 200 })),
    respondToPost: vi.fn(async () => ({ status: 200 })),
    replyToPost: vi.fn(async () => ({ status: 200 })),
    runOnboardingDemo: vi.fn(async () => ({ status: 200 })),
  },
}));

// TODO: This test hangs in CI (jsdom + userEvent.type is extremely slow).
// Needs to be rewritten with lighter rendering or moved to E2E.
describe.skip("ChatInput @ mention behavior", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  const agents = [
    {
      id: "agent-react",
      username: "react_ranger",
      agent_type: "core",
      team_name: "Core Team",
      team_id: "core",
      team_color: "#3B82F6",
    },
    {
      id: "agent-test",
      username: "test_agent",
      agent_type: "general",
      team_name: "Research",
      team_id: "research",
      team_color: "#8B5CF6",
    },
  ];

  it("shows mention suggestions when typing @ + query and inserts selected agent", async () => {
    const user = userEvent.setup();

    render(<ChatInput agents={agents} currentOrgId="org-1" />);

    const textarea = screen.getByPlaceholderText(
      /Type a message\.\.\. \(@ to mention a specific agent, Enter to send\)/,
    ) as HTMLTextAreaElement;

    await user.type(textarea, "@re");

    expect(
      await screen.findByText('@ Mention agents • "re"'),
    ).toBeInTheDocument();
    expect(screen.getByText("@react_ranger")).toBeInTheDocument();

    const selectedAgent = screen.getByText("@react_ranger");
    fireEvent.pointerDown(selectedAgent);

    expect(textarea.value).toBe("@react_ranger ");
  });

  it("does not treat @ inside plain symbols as a mention trigger", async () => {
    const user = userEvent.setup();
    const sendMessage = vi.mocked(api.postMessage);

    render(<ChatInput agents={agents} currentOrgId="org-1" />);

    const textarea = screen.getByPlaceholderText(
      /Type a message\.\.\. \(@ to mention a specific agent, Enter to send\)/,
    ) as HTMLTextAreaElement;

    await user.type(textarea, "ping me at team@example.com");

    expect(screen.queryByText("@ Mention agents")).not.toBeInTheDocument();

    const sendButton = screen.getByRole("button", { name: /send message/i });
    await user.click(sendButton);

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledTimes(1);
    });

    expect(sendMessage).toHaveBeenCalledWith(
      "ping me at team@example.com",
      expect.objectContaining({
        channel: "main",
      }),
    );

    expect(textarea.value).toBe("");
  });
});
