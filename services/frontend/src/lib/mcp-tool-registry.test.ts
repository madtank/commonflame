import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchMcpToolRegistry } from "./mcp-tool-registry";
import { storage } from "./storage";
import { __resetMcpDirectSessionForTests } from "./space-agent-api";

describe("fetchMcpToolRegistry", () => {
  beforeEach(() => {
    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockReturnValue("test-access-token");
    vi.spyOn(storage, "getCurrentSpaceId").mockReturnValue("space-123");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            agents: {
              tool_name: "agents",
              resource_uri: "ui://agents/dashboard",
              title: "Agent Dashboard",
            },
            tasks: {
              tool_name: "tasks",
              resource_uri: "ui://tasks/board",
              title: "Task Board",
            },
            "tasks/detail": {
              tool_name: "tasks",
              resource_uri: "ui://tasks/detail",
              title: "Task Detail",
            },
            plain_tool: {
              tool_name: "plain_tool",
            },
          }),
      }),
    );
  });

  afterEach(() => {
    __resetMcpDirectSessionForTests();
    vi.restoreAllMocks();
  });

  it("builds registry from the MCP app registry", async () => {
    const registry = await fetchMcpToolRegistry();

    expect(fetch).toHaveBeenCalledWith("/mcp-apps", {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    expect(Object.keys(registry)).toHaveLength(3);
    expect(registry["agents"]).toEqual({
      name: "agents",
      resourceUri: "ui://agents/dashboard",
      title: "Agent Dashboard",
    });
    expect(registry["tasks"]).toEqual({
      name: "tasks",
      resourceUri: "ui://tasks/board",
      title: "Task Board",
    });
    expect(registry["tasks/detail"]).toEqual({
      name: "tasks",
      resourceUri: "ui://tasks/detail",
      title: "Task Detail",
    });
  });

  it("excludes tools without widget URIs", async () => {
    const registry = await fetchMcpToolRegistry();

    expect(registry["plain_tool"]).toBeUndefined();
    expect(registry["no_meta_tool"]).toBeUndefined();
  });

  it("falls back to authenticated tools/list and handles _meta shape", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "mcp-session-id": "session-reg-1" }),
      text: () => Promise.resolve(JSON.stringify({ result: {} })),
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 202,
      text: () => Promise.resolve(""),
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      text: () =>
        Promise.resolve(
          JSON.stringify({
            jsonrpc: "2.0",
            id: "reg-2",
            result: {
              tools: [
                {
                  name: "whoami",
                  _meta: {
                    "openai/outputTemplate": "ui://agent-identity",
                  },
                },
              ],
            },
          }),
        ),
    });

    const registry = await fetchMcpToolRegistry();
    expect(registry["whoami"]?.resourceUri).toBe("ui://agent-identity");
    const [, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[3];
    expect(options.headers["Authorization"]).toBe(
      "Bearer test-access-token",
    );
    expect(options.headers["Mcp-Session-Id"]).toBe("session-reg-1");
    expect(options.headers["X-Space-Id"]).toBe("space-123");
  });

  it("handles ui.resourceUri shape (MCP Apps)", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "mcp-session-id": "session-reg-2" }),
      text: () => Promise.resolve(JSON.stringify({ result: {} })),
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 202,
      text: () => Promise.resolve(""),
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      text: () =>
        Promise.resolve(
          JSON.stringify({
            jsonrpc: "2.0",
            id: "reg-3",
            result: {
              tools: [
                {
                  name: "search",
                  meta: {
                    ui: { resourceUri: "ui://search-results" },
                  },
                },
              ],
            },
          }),
        ),
    });

    const registry = await fetchMcpToolRegistry();
    expect(registry["search"]?.resourceUri).toBe("ui://search-results");
  });

  it("handles empty tools list", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });

    const registry = await fetchMcpToolRegistry();
    expect(Object.keys(registry)).toHaveLength(0);
  });

  it("throws when both MCP app registry and tools/list fail", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "mcp-session-id": "session-reg-3" }),
      text: () => Promise.resolve(JSON.stringify({ result: {} })),
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 202,
      text: () => Promise.resolve(""),
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });

    await expect(fetchMcpToolRegistry()).rejects.toThrow(
      /MCP server returned 500/,
    );
  });

  it("handles SSE-framed tools/list response", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "mcp-session-id": "session-reg-4" }),
      text: () => Promise.resolve(JSON.stringify({ result: {} })),
    });
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 202,
      text: () => Promise.resolve(""),
    });
    const ssePayload = [
      "event: message",
      `data: ${JSON.stringify({
        jsonrpc: "2.0",
        id: "sse-reg",
        result: {
          tools: [
            {
              name: "context",
              meta: {
                "openai/outputTemplate": "ui://context-explorer",
              },
            },
          ],
        },
      })}`,
      "",
    ].join("\n");

    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve(ssePayload),
    });

    const registry = await fetchMcpToolRegistry();
    expect(registry["context"]?.resourceUri).toBe("ui://context-explorer");
  });
});
