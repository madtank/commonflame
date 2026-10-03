import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  __resetMcpDirectSessionForTests,
  normalizeMcpJsonRpcEnvelope,
  normalizeSpacesResponse,
  proxyMcpToolCall,
  readMcpAppResource,
  resolveMcpAppsUrl,
  resolveMcpDirectUrl,
  resolveMcpRequestUrl,
  proxyMcpResourceRead,
} from "./space-agent-api";
import { storage } from "./storage";

// ─── URL resolution ───────────────────────────────────────────

describe("resolveMcpDirectUrl", () => {
  it("returns /mcp-direct in browser dev mode (proxy to MCP server)", () => {
    // import.meta.env.DEV is true in test/dev
    expect(resolveMcpDirectUrl("http://127.0.0.1:8002")).toBe("/mcp-direct");
  });

  it("uses a different path than resolveMcpRequestUrl (API proxy)", () => {
    const directUrl = resolveMcpDirectUrl("http://127.0.0.1:8002");
    const apiUrl = resolveMcpRequestUrl("http://127.0.0.1:8002");
    expect(directUrl).not.toBe(apiUrl);
    expect(directUrl).toBe("/mcp-direct");
    expect(apiUrl).toBe("/mcp");
  });
});

describe("resolveMcpAppsUrl", () => {
  it("returns /mcp-apps in browser dev mode (proxy to MCP /apps)", () => {
    expect(resolveMcpAppsUrl("http://127.0.0.1:8002")).toBe("/mcp-apps");
    expect(resolveMcpAppsUrl("http://127.0.0.1:8002", "tasks")).toBe(
      "/mcp-apps/tasks",
    );
  });
});

// ─── Direct MCP resource reads ────────────────────────────────

describe("readMcpAppResource", () => {
  const WIDGET_HTML = "<html><body>Agent Dashboard</body></html>";

  beforeEach(() => {
    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockReturnValue("test-access-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: new Headers({
          "content-type": "text/html;profile=mcp-app",
        }),
        text: () => Promise.resolve(WIDGET_HTML),
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches widget HTML directly from MCP app registry via /mcp-apps", async () => {
    const result = await readMcpAppResource("ui://agents/dashboard");

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/mcp-apps/agents");
    expect(options.method).toBe("GET");
    expect(options.headers["Accept"]).toContain("text/html");
  });

  it("reads the canonical Task Detail resource for task-scoped detail URIs", async () => {
    await readMcpAppResource("ui://tasks/detail/activity-vision-task-complete");

    const [, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/mcp-apps/tasks/detail");
    expect(options.method).toBe("GET");
  });

  it("returns normalized widget resource with html content", async () => {
    const result = await readMcpAppResource("ui://agents/dashboard");

    expect(result.html).toBe(WIDGET_HTML);
    expect(result.resource_mime_type).toBe("text/html;profile=mcp-app");
    expect(result.title).toBe("agents/dashboard");
  });

  it("serves static app HTML without attaching bearer auth", async () => {
    await readMcpAppResource("ui://agents/dashboard");

    expect(fetch).toHaveBeenCalledTimes(1);
    const [, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.headers["Authorization"]).toBeUndefined();
  });

  it("does not use the JSON-RPC resource-read timeout budget", async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");

    await readMcpAppResource("ui://agents/dashboard");

    expect(setTimeoutSpy).not.toHaveBeenCalledWith(expect.any(Function), 30000);
  });

  it("throws on HTTP error from MCP server", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: () => Promise.resolve("Not Found"),
    });

    await expect(readMcpAppResource("ui://missing-widget")).rejects.toThrow(
      /MCP app server returned 404/,
    );
  });

  it("maps current MCP app resource URIs to /apps keys", async () => {
    await readMcpAppResource("ui://context/graph");
    await readMcpAppResource("ui://whoami/identity");

    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe(
      "/mcp-apps/context/graph",
    );
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[1][0]).toBe(
      "/mcp-apps/whoami",
    );
  });
});

// ─── proxyMcpResourceRead uses direct path ────────────────────

describe("proxyMcpResourceRead", () => {
  beforeEach(() => {
    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockReturnValue("test-access-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: new Headers({ "content-type": "text/html" }),
        text: () => Promise.resolve("<html>Search</html>"),
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses fetch (direct path) not apiClient", async () => {
    await proxyMcpResourceRead("ui://search/results");

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/mcp-apps/search");
  });

  it("returns a JSON-RPC-compatible resources/read result", async () => {
    const result = await proxyMcpResourceRead("ui://search/results");

    const [, options] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(options.method).toBe("GET");
    expect(result.contents?.[0]?.uri).toBe("ui://search/results");
    expect(result.contents?.[0]?.text).toBe("<html>Search</html>");
  });

  it("canonicalizes task-scoped detail URIs for iframe resource reads", async () => {
    await proxyMcpResourceRead("ui://tasks/detail/task-123");

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/mcp-apps/tasks/detail");
  });
});

// ─── Routing separation: tool calls vs app HTML ───────────────

describe("MCP routing separation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("widget HTML reads go to /mcp-apps, not JSON-RPC /mcp-direct", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: new Headers({ "content-type": "text/html" }),
        text: () => Promise.resolve("<html>Widget</html>"),
      }),
    );

    await readMcpAppResource("ui://test");

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/mcp-apps/test");
    expect(url).not.toBe("/mcp-direct");
    expect(url).not.toBe("/mcp");
  });
});

describe("proxyMcpToolCall auth recovery", () => {
  afterEach(() => {
    __resetMcpDirectSessionForTests();
    vi.restoreAllMocks();
  });

  it("uses the explicit active space id for direct MCP tool calls", async () => {
    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockReturnValue("test-access-token");
    vi.spyOn(storage, "getCurrentSpaceId").mockReturnValue("stale-space");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          headers: new Headers({ "mcp-session-id": "session-tool-1" }),
          text: () => Promise.resolve(JSON.stringify({ result: {} })),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 202,
          text: () => Promise.resolve(""),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          text: () =>
            Promise.resolve(
              JSON.stringify({
                jsonrpc: "2.0",
                id: "tool-explicit-space",
                result: { structuredContent: { state: "ready" } },
              }),
            ),
        }),
    );

    await proxyMcpToolCall(
      "spaces",
      { action: "list" },
      { spaceId: "team-hub-space" },
    );

    const initCall = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(initCall[1].headers["X-Space-Id"]).toBe("team-hub-space");
    const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[2];
    expect(call[1].headers["X-Space-Id"]).toBe("team-hub-space");
    expect(call[1].headers["Mcp-Session-Id"]).toBe("session-tool-1");
  });

  it("opens a fresh MCP session when the active space changes", async () => {
    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockReturnValue("test-access-token");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          headers: new Headers({ "mcp-session-id": "session-alpha" }),
          text: () => Promise.resolve(JSON.stringify({ result: {} })),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 202,
          text: () => Promise.resolve(""),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          text: () =>
            Promise.resolve(
              JSON.stringify({
                jsonrpc: "2.0",
                id: "tool-alpha",
                result: { structuredContent: { space: "alpha" } },
              }),
            ),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          headers: new Headers({ "mcp-session-id": "session-beta" }),
          text: () => Promise.resolve(JSON.stringify({ result: {} })),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 202,
          text: () => Promise.resolve(""),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          text: () =>
            Promise.resolve(
              JSON.stringify({
                jsonrpc: "2.0",
                id: "tool-beta",
                result: { structuredContent: { space: "beta" } },
              }),
            ),
        }),
    );

    await proxyMcpToolCall("tasks", { action: "list" }, { spaceId: "alpha" });
    await proxyMcpToolCall("tasks", { action: "list" }, { spaceId: "beta" });

    expect(fetch).toHaveBeenCalledTimes(6);
    const alphaToolCall = (fetch as ReturnType<typeof vi.fn>).mock.calls[2];
    const betaInitCall = (fetch as ReturnType<typeof vi.fn>).mock.calls[3];
    const betaToolCall = (fetch as ReturnType<typeof vi.fn>).mock.calls[5];
    expect(alphaToolCall[1].headers["Mcp-Session-Id"]).toBe("session-alpha");
    expect(betaInitCall[1].headers["X-Space-Id"]).toBe("beta");
    expect(betaInitCall[1].headers["Mcp-Session-Id"]).toBeUndefined();
    expect(betaToolCall[1].headers["Mcp-Session-Id"]).toBe("session-beta");
  });

  it("refreshes and retries when MCP session initialize returns 401", async () => {
    let currentToken = "stale-access-token";

    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockImplementation(() => currentToken);
    vi.spyOn(storage, "getCurrentSpaceId").mockReturnValue("space-123");
    vi.spyOn(storage, "refreshTokens").mockImplementation(async () => {
      currentToken = "fresh-access-token";
      return true;
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: false,
          status: 401,
          text: () => Promise.resolve("Unauthorized"),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          headers: new Headers({ "mcp-session-id": "session-fresh" }),
          text: () => Promise.resolve(JSON.stringify({ result: {} })),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 202,
          text: () => Promise.resolve(""),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          text: () =>
            Promise.resolve(
              JSON.stringify({
                jsonrpc: "2.0",
                id: "tool-after-refresh",
                result: { structuredContent: { ok: true } },
              }),
            ),
        }),
    );

    const result = await proxyMcpToolCall("tasks", { action: "list" });

    expect(fetch).toHaveBeenCalledTimes(4);
    const staleInit = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const freshInit = (fetch as ReturnType<typeof vi.fn>).mock.calls[1];
    const toolCall = (fetch as ReturnType<typeof vi.fn>).mock.calls[3];
    expect(staleInit[1].headers.Authorization).toBe(
      "Bearer stale-access-token",
    );
    expect(freshInit[1].headers.Authorization).toBe(
      "Bearer fresh-access-token",
    );
    expect(toolCall[1].headers.Authorization).toBe("Bearer fresh-access-token");
    expect(toolCall[1].headers["Mcp-Session-Id"]).toBe("session-fresh");
    expect(result).toEqual({ structuredContent: { ok: true } });
  });

  it("does not reuse an in-flight MCP session initialization for another space", async () => {
    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockReturnValue("test-access-token");

    let resolveAlphaInit: ((value: unknown) => void) | null = null;
    let resolveBetaInit: ((value: unknown) => void) | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, options: RequestInit) => {
        const headers = options.headers as Record<string, string>;
        const body = JSON.parse(String(options.body || "{}")) as {
          method?: string;
        };
        if (body.method === "initialize") {
          const response = {
            ok: true,
            status: 200,
            headers: new Headers({
              "mcp-session-id": `session-${headers["X-Space-Id"]}`,
            }),
            text: () => Promise.resolve(JSON.stringify({ result: {} })),
          };
          if (headers["X-Space-Id"] === "alpha") {
            return new Promise((resolve) => {
              resolveAlphaInit = resolve;
            });
          }
          if (headers["X-Space-Id"] === "beta") {
            return new Promise((resolve) => {
              resolveBetaInit = resolve;
            });
          }
          return Promise.resolve(response);
        }
        if (body.method === "notifications/initialized") {
          return Promise.resolve({
            ok: true,
            status: 202,
            text: () => Promise.resolve(""),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          text: () =>
            Promise.resolve(
              JSON.stringify({
                jsonrpc: "2.0",
                id: `tool-${headers["X-Space-Id"]}`,
                result: {
                  structuredContent: {
                    space: headers["X-Space-Id"],
                    session: headers["Mcp-Session-Id"],
                  },
                },
              }),
            ),
        });
      }),
    );

    const alphaPromise = proxyMcpToolCall(
      "tasks",
      { action: "list" },
      { spaceId: "alpha" },
    );
    await Promise.resolve();
    expect(resolveAlphaInit).toBeTypeOf("function");

    const betaPromise = proxyMcpToolCall(
      "tasks",
      { action: "list" },
      { spaceId: "beta" },
    );
    await Promise.resolve();
    expect(resolveBetaInit).toBeTypeOf("function");

    resolveBetaInit?.({
      ok: true,
      status: 200,
      headers: new Headers({ "mcp-session-id": "session-beta" }),
      text: () => Promise.resolve(JSON.stringify({ result: {} })),
    });
    await expect(betaPromise).resolves.toEqual({
      structuredContent: { space: "beta", session: "session-beta" },
    });

    resolveAlphaInit?.({
      ok: true,
      status: 200,
      headers: new Headers({ "mcp-session-id": "session-alpha" }),
      text: () => Promise.resolve(JSON.stringify({ result: {} })),
    });
    await expect(alphaPromise).resolves.toEqual({
      structuredContent: { space: "alpha", session: "session-alpha" },
    });

    const initializeCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls
      .map(([, options]) => ({
        headers: options.headers as Record<string, string>,
        body: JSON.parse(String(options.body || "{}")) as { method?: string },
      }))
      .filter((call) => call.body.method === "initialize");
    expect(initializeCalls.map((call) => call.headers["X-Space-Id"])).toEqual([
      "alpha",
      "beta",
    ]);
  });

  it("refreshes and retries when MCP returns an auth-style tool error inside a 200 response", async () => {
    let currentToken = "stale-access-token";

    vi.spyOn(storage, "isTokenExpired").mockReturnValue(false);
    vi.spyOn(storage, "getUserToken").mockImplementation(() => currentToken);
    vi.spyOn(storage, "getCurrentSpaceId").mockReturnValue("space-123");
    vi.spyOn(storage, "refreshTokens").mockImplementation(async () => {
      currentToken = "fresh-access-token";
      return true;
    });

    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          headers: new Headers({ "mcp-session-id": "session-stale" }),
          text: () => Promise.resolve(JSON.stringify({ result: {} })),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 202,
          text: () => Promise.resolve(""),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          text: () =>
            Promise.resolve(
              JSON.stringify({
                jsonrpc: "2.0",
                id: "tool-1",
                result: {
                  isError: true,
                  content: [
                    {
                      type: "text",
                      text: "Authentication required for tools/call.",
                    },
                  ],
                },
              }),
            ),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          headers: new Headers({ "mcp-session-id": "session-fresh" }),
          text: () => Promise.resolve(JSON.stringify({ result: {} })),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 202,
          text: () => Promise.resolve(""),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          text: () =>
            Promise.resolve(
              JSON.stringify({
                jsonrpc: "2.0",
                id: "tool-2",
                result: {
                  structuredContent: {
                    kind: "task_collection",
                    data: { items: [], total: 0 },
                  },
                },
              }),
            ),
        }),
    );

    const result = await proxyMcpToolCall("tasks", { action: "list" });

    expect(fetch).toHaveBeenCalledTimes(6);
    const firstCall = (fetch as ReturnType<typeof vi.fn>).mock.calls[2];
    const secondCall = (fetch as ReturnType<typeof vi.fn>).mock.calls[5];
    expect(firstCall[1].headers.Authorization).toBe(
      "Bearer stale-access-token",
    );
    expect(firstCall[1].headers["X-Space-Id"]).toBe("space-123");
    expect(firstCall[1].headers["Mcp-Session-Id"]).toBe("session-stale");
    expect(secondCall[1].headers.Authorization).toBe(
      "Bearer fresh-access-token",
    );
    expect(secondCall[1].headers["X-Space-Id"]).toBe("space-123");
    expect(secondCall[1].headers["Mcp-Session-Id"]).toBe("session-fresh");
    expect(result).toEqual({
      structuredContent: {
        kind: "task_collection",
        data: { items: [], total: 0 },
      },
    });
  });
});

describe("normalizeSpacesResponse", () => {
  it("accepts nested spaces envelopes used by dev/staging", () => {
    expect(
      normalizeSpacesResponse({
        current_space_id: "beta",
        result: {
          spaces: [
            { id: "alpha", name: "Alpha" },
            { id: "beta", name: "Beta" },
          ],
        },
      }),
    ).toEqual([
      { id: "alpha", name: "Alpha", is_current: false },
      { id: "beta", name: "Beta", is_current: true },
    ]);
  });

  it("lets the database current id override stale list flags", () => {
    expect(
      normalizeSpacesResponse({
        current_space_id: "db-current",
        spaces: [
          { id: "prediction-lab", name: "Prediction Lab", is_current: true },
          { id: "db-current", name: "DB Current Space", is_current: false },
        ],
      }),
    ).toEqual([
      { id: "prediction-lab", name: "Prediction Lab", is_current: false },
      { id: "db-current", name: "DB Current Space", is_current: true },
    ]);
  });

  it("uses current_space as a non-blank fallback", () => {
    expect(
      normalizeSpacesResponse({
        current_space: { id: "current", name: "Current Space" },
      }),
    ).toEqual([{ id: "current", name: "Current Space", is_current: true }]);
  });
});
