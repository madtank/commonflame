import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen, userEvent } from "@/test/utils";

import { AxSettingsDialog } from "./AxSettingsDialog";

const {
  getUserProfileMock,
  getAgentsMock,
  listAgentKeysMock,
  apiClientGetMock,
} = vi.hoisted(() => ({
  getUserProfileMock: vi.fn(),
  getAgentsMock: vi.fn(),
  listAgentKeysMock: vi.fn(),
  apiClientGetMock: vi.fn(),
}));

vi.mock("@/lib/api-clean", () => ({
  api: {
    getUserProfile: getUserProfileMock,
    getAgents: getAgentsMock,
    listAgentKeys: listAgentKeysMock,
    createAgentKey: vi.fn(),
    rotateAgentKey: vi.fn(),
    revokeAgentKey: vi.fn(),
  },
  apiClient: {
    get: apiClientGetMock,
  },
  createPersonalAccessKey: vi.fn(),
  listPatScopeAgents: vi.fn(async () => []),
  listPersonalAccessKeys: vi.fn(async () => []),
  revokePersonalAccessKey: vi.fn(),
  rotatePersonalAccessKey: vi.fn(),
}));

vi.mock("@/hooks/useUserSettings", () => ({
  useUserSettings: () => ({
    settings: {
      compact_mode: false,
      ai_auto_summarize: false,
      theme: "system",
    },
    saving: false,
    updateSettings: vi.fn(async () => undefined),
  }),
}));

vi.mock("@/hooks/usePresence", () => ({
  usePresence: () => ({
    presence: {},
    getStatus: () => "offline",
    getFreshness: () => 0,
    isLoading: false,
    refresh: vi.fn(),
  }),
}));

describe("AxSettingsDialog agents", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllEnvs();
    getUserProfileMock.mockResolvedValue({
      id: "user-1",
      username: "madtank",
      email: "madtank@example.com",
    });
    getAgentsMock.mockResolvedValue({
      agents: [],
      total_count: 0,
      limit: 100,
      offset: 0,
      has_more: false,
    });
    listAgentKeysMock.mockResolvedValue([]);
    apiClientGetMock.mockResolvedValue({ data: { violations: [] } });
  });

  it("hides the legacy User Token option in Credentials", async () => {
    const user = userEvent.setup();

    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    await user.click(screen.getByRole("tab", { name: /credentials/i }));

    expect(screen.getByText("Agent Token")).toBeInTheDocument();
    expect(screen.queryByText("User Token")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /create user token/i }),
    ).not.toBeInTheDocument();
  });

  it("hides agent client credentials by default", () => {
    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    expect(
      screen.queryByRole("tab", { name: /agents/i }),
    ).not.toBeInTheDocument();
    expect(getAgentsMock).not.toHaveBeenCalled();
    expect(listAgentKeysMock).not.toHaveBeenCalled();
  });

  it("shows an explicit unsupported state when the owned-agent endpoint is missing", async () => {
    vi.stubEnv("VITE_ENABLE_AGENT_M2M_CREDENTIALS", "true");
    const user = userEvent.setup();
    getAgentsMock.mockRejectedValueOnce({
      response: { status: 404 },
    });

    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    await user.click(screen.getByRole("tab", { name: /agents/i }));

    expect(
      await screen.findByText(
        /Owned-agent management is not available from this backend yet/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/Owned-agent endpoint unavailable/i),
    ).toBeDisabled();
    expect(
      screen.getByText(
        /headless credential management is unavailable here yet/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No owned agents are available to manage here yet\./i),
    ).not.toBeInTheDocument();
  });

  it("names the headless credential endpoint when agent keys are unsupported", async () => {
    vi.stubEnv("VITE_ENABLE_AGENT_M2M_CREDENTIALS", "true");
    const user = userEvent.setup();
    getAgentsMock.mockResolvedValueOnce({
      agents: [
        {
          id: "agent-1",
          name: "frontend_sentinel",
          status: "online",
          visibility: "private",
          last_active_at: "2026-03-22T00:00:00Z",
        },
      ],
      total_count: 1,
      limit: 100,
      offset: 0,
      has_more: false,
    });
    listAgentKeysMock.mockRejectedValueOnce({
      response: { status: 404 },
    });

    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    await user.click(screen.getByRole("tab", { name: /agents/i }));

    expect(await screen.findAllByText("frontend_sentinel")).toHaveLength(2);
    expect(
      await screen.findByText(/\/api\/v1\/agents\/:agentId\/keys/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create credential/i }),
    ).toBeDisabled();
  });
});
