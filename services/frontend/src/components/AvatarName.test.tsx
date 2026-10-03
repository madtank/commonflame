import { describe, it, expect, beforeEach, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "@/test/utils";
import { AxiosError } from "axios";

import { AvatarName } from "./AvatarName";

vi.mock("@/lib/api-clean", () => ({
  fetchAgentSummary: vi.fn(),
  fetchUserSummary: vi.fn(),
}));

import { fetchAgentSummary } from "@/lib/api-clean";

describe("AvatarName", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows agent owner details when quick panel opens", async () => {
    vi.mocked(fetchAgentSummary).mockResolvedValue({
      id: "agent-123",
      name: "Ops Scout",
      owner: { id: "user-1", handle: "madtank", name: "Matt D." },
      last_active_at: "2025-01-05T12:00:00Z",
      visibility: "org_visible",
    });

    render(
      <AvatarName
        type="agent"
        id="agent-123"
        displayName="@ops_scout"
        spaceId="space-1"
        ownerHandle="madtank"
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /ops_scout/i }));

    await waitFor(() => {
      expect(screen.getByText("@madtank")).toBeInTheDocument();
    });

    expect(fetchAgentSummary).toHaveBeenCalledWith("agent-123", "space-1");
    expect(screen.getByText(/Last active:/i)).toBeInTheDocument();
  });

  it("shows fallback owner details when agent summary is private", async () => {
    const error = Object.assign(new Error("Forbidden"), {
      isAxiosError: true,
      response: { status: 403 },
    }) as AxiosError;
    vi.mocked(fetchAgentSummary).mockRejectedValue(error);

    render(
      <AvatarName
        type="agent"
        id="agent-private"
        displayName="@secret_bot"
        spaceId="space-99"
        ownerHandle="madtank"
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /secret_bot/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/This agent is marked private/i),
      ).toBeInTheDocument();
      expect(screen.getByText("@madtank")).toBeInTheDocument();
    });
  });

  it("appends a mention when send message is clicked from the agent card", async () => {
    vi.mocked(fetchAgentSummary).mockResolvedValue({
      id: "agent-123",
      name: "Ops Scout",
      owner: { id: "user-1", handle: "madtank", name: "Matt D." },
      last_active_at: "2025-01-05T12:00:00Z",
      visibility: "org_visible",
    });

    const listener = vi.fn();
    window.addEventListener(
      "ax:agent-mention-append",
      listener as EventListener,
    );

    render(
      <AvatarName
        type="agent"
        id="agent-123"
        displayName="@ops_scout"
        spaceId="space-1"
        ownerHandle="madtank"
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /ops_scout/i }));
    const sendButton = await screen.findByRole("button", {
      name: /send message to ops_scout/i,
    });
    await userEvent.click(sendButton);

    expect(listener).toHaveBeenCalledTimes(1);
    const event = listener.mock.calls[0]?.[0] as CustomEvent<{
      handle: string;
    }>;
    expect(event.detail.handle).toBe("ops_scout");

    window.removeEventListener(
      "ax:agent-mention-append",
      listener as EventListener,
    );
  });
});
