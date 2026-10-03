import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen } from "@/test/utils";
import { AxSettingsDialog } from "./AxSettingsDialog";

const { getUserProfileMock, getAgentsMock, listAgentKeysMock } = vi.hoisted(
  () => ({
    getUserProfileMock: vi.fn(),
    getAgentsMock: vi.fn(),
    listAgentKeysMock: vi.fn(),
  }),
);

vi.mock("@/lib/api-clean", () => ({
  api: {
    getUserProfile: getUserProfileMock,
    getAgents: getAgentsMock,
    listAgentKeys: listAgentKeysMock,
    createAgentKey: vi.fn(),
    rotateAgentKey: vi.fn(),
    revokeAgentKey: vi.fn(),
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

describe("AxSettingsDialog profile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getUserProfileMock.mockResolvedValue({
      id: "user-1",
      username: "adminuser",
      email: "admin@example.com",
      role: "admin",
      platform_features: {
        subscription_tier: "plus",
      },
    });
    getAgentsMock.mockResolvedValue({
      agents: [],
      total_count: 0,
      limit: 100,
      offset: 0,
      has_more: false,
    });
    listAgentKeysMock.mockResolvedValue([]);
  });

  it("shows role and tier badges plus the admin panel link", async () => {
    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    expect(await screen.findByText(/Role: admin/i)).toBeInTheDocument();
    expect(screen.getByText(/Tier: plus/i)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /open admin panel/i }),
    ).toHaveAttribute("href", "/admin");
  });
});
