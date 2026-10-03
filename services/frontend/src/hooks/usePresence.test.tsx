import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { usePresence } from "@/hooks/usePresence";

const { getMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
}));

vi.mock("@/lib/api-clean", () => ({
  apiClient: {
    get: getMock,
  },
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

function DualPresenceConsumer() {
  const first = usePresence("space-1");
  const second = usePresence("space-1");

  return (
    <div>
      <span data-testid="first-status">{first.getStatus("dev_sentinel")}</span>
      <span data-testid="second-status">
        {second.getStatus("@dev_sentinel")}
      </span>
    </div>
  );
}

function SpacePresenceConsumer({ spaceId }: { spaceId: string }) {
  const presence = usePresence(spaceId);

  return (
    <span data-testid="space-status">
      {presence.getStatus("@dev_sentinel")}
    </span>
  );
}

describe("usePresence", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("shares a single per-space presence request across consumers", async () => {
    getMock.mockResolvedValue({
      data: {
        agents: [
          {
            agent_id: "agent-1",
            name: "dev_sentinel",
            presence: "online",
            responsive: true,
            last_active: new Date().toISOString(),
            agent_type: "assistant",
          },
        ],
      },
    });

    render(<DualPresenceConsumer />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("first-status")).toHaveTextContent("active");
      expect(screen.getByTestId("second-status")).toHaveTextContent("active");
    });

    expect(getMock).toHaveBeenCalledTimes(1);
    expect(getMock).toHaveBeenCalledWith("/api/v1/agents/presence", {
      params: { space_id: "space-1" },
    });
  });

  it("does not leak previous-space presence while a new space is loading", async () => {
    getMock
      .mockResolvedValueOnce({
        data: {
          agents: [
            {
              agent_id: "agent-1",
              name: "dev_sentinel",
              presence: "online",
              responsive: true,
              last_active: new Date().toISOString(),
              agent_type: "assistant",
            },
          ],
        },
      })
      .mockImplementationOnce(() => new Promise(() => undefined));

    const wrapper = createWrapper();
    const view = render(<SpacePresenceConsumer spaceId="space-1" />, {
      wrapper,
    });

    await waitFor(() => {
      expect(screen.getByTestId("space-status")).toHaveTextContent("active");
    });

    view.rerender(<SpacePresenceConsumer spaceId="space-2" />);

    await waitFor(() => {
      expect(screen.getByTestId("space-status")).toHaveTextContent("offline");
    });
  });

  it("falls back to legacy heartbeats when the bulk presence endpoint is unavailable", async () => {
    getMock
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({
        data: {
          "heartbeat:dev_sentinel": {
            value: JSON.stringify({
              agent: "dev_sentinel",
              model: "gpt-5.4",
              status: "active",
              ts: new Date().toISOString(),
              last_task: "test",
              capabilities: [],
            }),
          },
        },
      });

    render(<DualPresenceConsumer />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("first-status")).toHaveTextContent("active");
    });

    expect(getMock).toHaveBeenNthCalledWith(1, "/api/v1/agents/presence", {
      params: { space_id: "space-1" },
    });
    expect(getMock).toHaveBeenNthCalledWith(2, "/api/v1/context", {
      params: { topic: "presence", prefix: "heartbeat" },
    });
  });
});
