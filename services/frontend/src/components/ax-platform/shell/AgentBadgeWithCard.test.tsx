import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AgentBadgeWithCard } from "./AgentBadgeWithCard";

// Mock fetchAgentSummary
const mockFetchAgentSummary = vi.fn().mockResolvedValue({
  id: "agent-1",
  name: "aX",
  owner: { id: "owner-1", handle: "madtank", name: "madtank" },
  last_active_at: "2026-03-14T12:00:00Z",
});

vi.mock("@/lib/api-clean", () => ({
  fetchAgentSummary: (...args: unknown[]) => mockFetchAgentSummary(...args),
  fetchUserSummary: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { attributes: { id: "owner-1" } } }),
}));

const mockGetControlState = vi
  .fn()
  .mockResolvedValue({ is_disabled: false, disabled_by: [] });
const mockUpdateControl = vi.fn();

vi.mock("@/services/agentControlService", () => ({
  agentControlService: {
    getControlState: (...args: unknown[]) => mockGetControlState(...args),
    updateControl: (...args: unknown[]) => mockUpdateControl(...args),
  },
}));

describe("AgentBadgeWithCard", () => {
  const defaultProps = {
    name: "aX",
    emoji: "✨",
    agentId: "agent-1",
    spaceId: "space-1",
    onMention: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchAgentSummary.mockResolvedValue({
      id: "agent-1",
      name: "aX",
      owner: { id: "owner-1", handle: "madtank", name: "madtank" },
      last_active_at: "2026-03-14T12:00:00Z",
    });
    mockGetControlState.mockResolvedValue({
      is_disabled: false,
      disabled_by: [],
    });
    mockUpdateControl.mockResolvedValue({
      is_disabled: false,
      disabled_by: [],
    });
  });

  it("renders the agent name in the badge", () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    expect(screen.getByText("aX")).toBeInTheDocument();
  });

  it("uses a high-contrast clickable badge treatment in light mode", () => {
    render(<AgentBadgeWithCard {...defaultProps} />);

    const badgeButton = screen.getByRole("button", { name: /view.*aX/i });
    expect(badgeButton.className).toContain("cursor-pointer");

    const badgePill = badgeButton.firstElementChild;
    expect(badgePill?.className).toContain("border-slate-300");
    expect(badgePill?.className).toContain("text-slate-800");
    expect(badgePill?.className).toContain("hover:border-cyan-300");
    expect(badgePill?.className).toContain("hover:bg-cyan-50");
  });

  it("renders a presence indicator when status is provided", () => {
    render(
      <AgentBadgeWithCard
        {...defaultProps}
        presenceStatus="active"
        presenceFreshness={1}
      />,
    );

    expect(screen.getByRole("status", { name: "Active" })).toBeInTheDocument();
  });

  it("opens agent card dialog on click", async () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    const badge = screen.getByRole("button", { name: /view.*aX/i });
    fireEvent.click(badge);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("does NOT insert mention on click (dialog opens instead)", () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    const badge = screen.getByRole("button", { name: /view.*aX/i });
    fireEvent.click(badge);
    expect(defaultProps.onMention).not.toHaveBeenCalled();
  });

  it("fetches agent summary when dialog opens", async () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    fireEvent.click(screen.getByRole("button", { name: /view.*aX/i }));

    await waitFor(() => {
      expect(mockFetchAgentSummary).toHaveBeenCalledWith("agent-1", "space-1");
    });
  });

  it("shows owner info when summary is loaded", async () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    fireEvent.click(screen.getByRole("button", { name: /view.*aX/i }));

    expect(await screen.findByText("Owner")).toBeInTheDocument();
    expect(await screen.findByText("@madtank")).toBeInTheDocument();
  });

  it("shows kill switch for owned agents", async () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    fireEvent.click(screen.getByRole("button", { name: /view.*aX/i }));

    // Wait for summary fetch (determines ownership), then control state fetch
    await waitFor(() => {
      expect(mockGetControlState).toHaveBeenCalledWith("agent-1");
    });

    expect(await screen.findByText(/state:\s*active/i)).toBeInTheDocument();
  });

  it("shows space-owned controls without exposing the system owner", async () => {
    mockFetchAgentSummary.mockResolvedValue({
      id: "agent-1",
      name: "aX",
      owner: { id: "__system__", handle: "__system__", name: "System" },
      origin: "space_agent",
      agent_type: "space_agent",
      control: {
        is_disabled: false,
        disabled_by: [],
      },
    });
    mockGetControlState.mockRejectedValue(new Error("403 Forbidden"));

    render(<AgentBadgeWithCard {...defaultProps} />);
    fireEvent.click(screen.getByRole("button", { name: /view.*aX/i }));

    expect(await screen.findByText("Space-owned agent")).toBeInTheDocument();
    expect(screen.getByText("Owned by this space")).toBeInTheDocument();
    expect(screen.queryByText("@__system__")).not.toBeInTheDocument();
    expect(await screen.findByText(/state:\s*active/i)).toBeInTheDocument();
  });

  it("hides kill switch for non-owned agents", async () => {
    // Different owner ID
    mockFetchAgentSummary.mockResolvedValue({
      id: "agent-2",
      name: "Other Agent",
      owner: { id: "other-owner", handle: "someone", name: "someone" },
    });
    // Backend is authoritative: getControlState rejects for viewers who
    // aren't the owner (or admin). Simulate the 403 we'd get from the real
    // /agents/{id}/control endpoint. This must not bubble up as a UI error.
    mockGetControlState.mockRejectedValue(new Error("403 Forbidden"));

    render(
      <AgentBadgeWithCard
        {...defaultProps}
        name="Other Agent"
        agentId="agent-2"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /view.*Other/i }));

    // Wait for summary to load
    await waitFor(() => {
      expect(mockFetchAgentSummary).toHaveBeenCalled();
    });
    // Control fetch is also attempted (server decides authorization).
    await waitFor(() => {
      expect(mockGetControlState).toHaveBeenCalledWith("agent-2");
    });

    // Kill switch should NOT appear (backend refused control access)
    expect(screen.queryByText(/state:\s*active/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/state:\s*disabled/i)).not.toBeInTheDocument();
  });

  it("provides a message action inside the dialog", async () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    fireEvent.click(screen.getByRole("button", { name: /view.*aX/i }));

    const messageBtn = await screen.findByRole("button", {
      name: /send message/i,
    });
    expect(messageBtn).toBeInTheDocument();
  });

  it("calls onMention when the dialog message action is used", async () => {
    render(<AgentBadgeWithCard {...defaultProps} />);
    fireEvent.click(screen.getByRole("button", { name: /view.*aX/i }));

    const messageBtn = await screen.findByRole("button", {
      name: /send message/i,
    });
    fireEvent.click(messageBtn);

    expect(defaultProps.onMention).toHaveBeenCalledWith("aX");
  });
});
