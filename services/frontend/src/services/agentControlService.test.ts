import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentControlService } from "@/services/agentControlService";
import { apiClient } from "@/lib/api-clean";
import { storage } from "@/lib/storage";

vi.mock("@/lib/api-clean", () => ({
  apiClient: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

vi.mock("@/lib/storage", () => ({
  storage: {
    getCurrentSpaceId: vi.fn(),
  },
}));

describe("agentControlService", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiClient.get).mockResolvedValue({ data: {} });
    vi.mocked(apiClient.patch).mockResolvedValue({ data: {} });
  });

  it("sends selected space context on control updates", async () => {
    vi.mocked(storage.getCurrentSpaceId).mockReturnValue("space-123");

    await agentControlService.disableAgent("agent-123", "maintenance");

    expect(apiClient.patch).toHaveBeenCalledWith(
      "/auth/agents/agent-123/control",
      {
        scope: "agent",
        disabled: true,
        disabled_until: null,
        reason: "maintenance",
      },
      {
        headers: { "X-Space-Id": "space-123" },
        params: { space_id: "space-123" },
      },
    );
  });

  it("clears all suppressive control modes when re-enabling an agent", async () => {
    vi.mocked(storage.getCurrentSpaceId).mockReturnValue("space-123");

    await agentControlService.enableAgent("agent-123");

    expect(apiClient.patch).toHaveBeenCalledWith(
      "/auth/agents/agent-123/control",
      {
        scope: "agent",
        disabled: false,
        disabled_until: null,
        reason: null,
        no_reply: false,
        no_reply_reason: null,
        no_reply_until: null,
        routing_only: false,
        routing_only_reason: null,
        routing_only_until: null,
      },
      {
        headers: { "X-Space-Id": "space-123" },
        params: { space_id: "space-123" },
      },
    );
  });
});
