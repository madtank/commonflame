import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resolveSpaceAgentWidget,
  resolveSpaceAgentWidgetFromMcp,
} from "@/lib/space-agent-api";
import {
  resolveWidgetResource,
  resolveWidgetResourceViaCanonicalApi,
  resetWidgetResourceResolverForTests,
} from "./widget-resource-resolver";

vi.mock("@/lib/space-agent-api", () => ({
  resolveSpaceAgentWidget: vi.fn(),
  resolveSpaceAgentWidgetFromMcp: vi.fn(),
}));

const payload = {
  space_id: "11111111-1111-1111-1111-111111111111",
  message_id: "22222222-2222-4222-8222-222222222222",
  resource_uri: "ui://task-board",
  tool_name: "tasks",
  tool_call_id: "call-1",
};

const launcherPayload = {
  ...payload,
  message_id: "launcher-tasks",
  resource_uri: "ui://tasks/board",
};

describe("widget-resource-resolver", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetWidgetResourceResolverForTests();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    resetWidgetResourceResolverForTests();
  });

  it("dedupes concurrent requests for the same widget resource", async () => {
    const resolver = vi.fn(async () => ({
      html: "<div>ok</div>",
    }));

    const first = resolveWidgetResource(payload, resolver);
    const second = resolveWidgetResource(payload, resolver);

    await expect(Promise.all([first, second])).resolves.toEqual([
      { html: "<div>ok</div>" },
      { html: "<div>ok</div>" },
    ]);
    expect(resolver).toHaveBeenCalledTimes(1);
  });

  it("retries retryable rate-limit errors before succeeding", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    const resolver = vi
      .fn()
      .mockRejectedValueOnce({ response: { status: 429 } })
      .mockResolvedValueOnce({ html: "<div>retried</div>" });

    const request = resolveWidgetResource(payload, resolver);
    await vi.runAllTimersAsync();

    await expect(request).resolves.toEqual({ html: "<div>retried</div>" });
    expect(resolver).toHaveBeenCalledTimes(2);
  });

  it("caches successful results for later requests", async () => {
    const resolver = vi.fn(async () => ({
      html: "<div>cached</div>",
    }));

    await expect(resolveWidgetResource(payload, resolver)).resolves.toEqual({
      html: "<div>cached</div>",
    });
    await expect(resolveWidgetResource(payload, resolver)).resolves.toEqual({
      html: "<div>cached</div>",
    });

    expect(resolver).toHaveBeenCalledTimes(1);
  });

  it("prefers the canonical backend widget resolver", async () => {
    vi.mocked(resolveSpaceAgentWidget).mockResolvedValueOnce({
      html: "<!DOCTYPE html><html><body>api</body></html>",
    });

    await expect(
      resolveWidgetResourceViaCanonicalApi(payload),
    ).resolves.toEqual({
      html: "<!DOCTYPE html><html><body>api</body></html>",
    });

    expect(resolveSpaceAgentWidget).toHaveBeenCalledWith(payload);
    expect(resolveSpaceAgentWidgetFromMcp).not.toHaveBeenCalled();
  });

  it("clears the canonical resolver timeout after a successful API response", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout");
    vi.mocked(resolveSpaceAgentWidget).mockResolvedValueOnce({
      html: "<!DOCTYPE html><html><body>api</body></html>",
    });

    try {
      await expect(
        resolveWidgetResourceViaCanonicalApi(payload),
      ).resolves.toEqual({
        html: "<!DOCTYPE html><html><body>api</body></html>",
      });

      expect(clearTimeoutSpy).toHaveBeenCalled();
    } finally {
      clearTimeoutSpy.mockRestore();
    }
  });

  it("falls back to direct MCP reads when the backend returns legacy widget shell HTML", async () => {
    vi.mocked(resolveSpaceAgentWidget).mockResolvedValueOnce({
      html: '<div class="ax-mcp-widget"><p class="ax-widget-placeholder">Loading widget from <code>ui://tasks/board</code>&hellip;</p></div>',
      tool_call_id: "call-1",
      initial_data: {
        kind: "task_list",
        data: {
          tasks: [{ id: "task-1", title: "Keep hydration metadata" }],
        },
      },
    });
    vi.mocked(resolveSpaceAgentWidgetFromMcp).mockResolvedValueOnce({
      html: "<!DOCTYPE html><html><body>mcp</body></html>",
    });

    await expect(
      resolveWidgetResourceViaCanonicalApi(payload),
    ).resolves.toEqual({
      html: "<!DOCTYPE html><html><body>mcp</body></html>",
      tool_call_id: "call-1",
      initial_data: {
        kind: "task_list",
        data: {
          tasks: [{ id: "task-1", title: "Keep hydration metadata" }],
        },
      },
    });

    expect(resolveSpaceAgentWidget).toHaveBeenCalledWith(payload);
    expect(resolveSpaceAgentWidgetFromMcp).toHaveBeenCalledWith(payload);
  });

  it("skips the canonical backend resolver for launcher-style synthetic message ids", async () => {
    vi.mocked(resolveSpaceAgentWidgetFromMcp).mockResolvedValueOnce({
      html: "<div>launcher</div>",
    });

    await expect(
      resolveWidgetResourceViaCanonicalApi(launcherPayload),
    ).resolves.toEqual({
      html: "<div>launcher</div>",
    });

    expect(resolveSpaceAgentWidget).not.toHaveBeenCalled();
    expect(resolveSpaceAgentWidgetFromMcp).toHaveBeenCalledWith(
      launcherPayload,
    );
  });

  it("falls back to direct MCP resource reads when the API resolver fails", async () => {
    vi.mocked(resolveSpaceAgentWidget).mockRejectedValueOnce(
      new Error("backend widget resolve failed"),
    );
    vi.mocked(resolveSpaceAgentWidgetFromMcp).mockResolvedValueOnce({
      html: "<div>mcp</div>",
    });

    await expect(
      resolveWidgetResourceViaCanonicalApi(payload),
    ).resolves.toEqual({
      html: "<div>mcp</div>",
    });

    expect(resolveSpaceAgentWidget).toHaveBeenCalledWith(payload);
    expect(resolveSpaceAgentWidgetFromMcp).toHaveBeenCalledWith(payload);
  });

  it("falls back to direct MCP resource reads when the API resolver times out", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.mocked(resolveSpaceAgentWidget).mockImplementationOnce(
      () => new Promise(() => {}),
    );
    vi.mocked(resolveSpaceAgentWidgetFromMcp).mockResolvedValueOnce({
      html: "<div>fallback</div>",
    });

    const request = resolveWidgetResourceViaCanonicalApi(payload);
    await vi.advanceTimersByTimeAsync(10000);

    await expect(request).resolves.toEqual({
      html: "<div>fallback</div>",
    });

    expect(resolveSpaceAgentWidget).toHaveBeenCalledWith(payload);
    expect(resolveSpaceAgentWidgetFromMcp).toHaveBeenCalledWith(payload);
  });
});
