import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildUploadContextKey,
  getSpaceAgentDirectory,
  normalizeMcpJsonRpcEnvelope,
  resolveMcpRequestUrl,
  sendSpaceAgentMessage,
  switchSpaceAgentSpace,
  storeUploadInContext,
} from "./space-agent-api";
import { apiClient } from "@/lib/api-clean";
import { storage } from "./storage";

describe("normalizeMcpJsonRpcEnvelope", () => {
  it("parses plain JSON-RPC payloads", () => {
    expect(
      normalizeMcpJsonRpcEnvelope(
        '{"jsonrpc":"2.0","id":"1","result":{"ok":true}}',
      ),
    ).toEqual({
      jsonrpc: "2.0",
      id: "1",
      result: { ok: true },
    });
  });

  it("parses streamable-http MCP responses with event/data framing", () => {
    expect(
      normalizeMcpJsonRpcEnvelope(
        'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":{"content":[{"type":"text","text":"ok"}]}}\n\n',
      ),
    ).toEqual({
      jsonrpc: "2.0",
      id: "1",
      result: {
        content: [{ type: "text", text: "ok" }],
      },
    });
  });

  it("throws on invalid payloads", () => {
    expect(() => normalizeMcpJsonRpcEnvelope("not-json")).toThrow(
      /Invalid MCP response payload/,
    );
  });
});

describe("resolveMcpRequestUrl", () => {
  it("uses the same-origin MCP proxy in browser dev", () => {
    expect(resolveMcpRequestUrl("http://127.0.0.1:8002")).toBe("/mcp");
  });
});

// UPLOADS-CONTEXT-001 — storeUploadInContext
vi.mock("@/lib/api-clean", () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn().mockResolvedValue({ data: { status: "stored" } }),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiClient.post).mockResolvedValue({ data: { status: "stored" } });
});

describe("storeUploadInContext (UPLOADS-CONTEXT-001)", () => {
  it("is exported and callable", () => {
    expect(typeof storeUploadInContext).toBe("function");
  });

  it("returns the stored context key (best-effort pattern)", async () => {
    await expect(
      storeUploadInContext("space-1", {
        id: "att-1",
        filename: "test.png",
        content_type: "image/png",
        size_bytes: 100,
        url: "/uploads/files/test.png",
        uploaded_at: "2026-04-11T00:06:00.000Z",
      }),
    ).resolves.toBe("upload:1775865960000:test.png:att-1");
  });

  it("uses uploaded_at to make the stored key deterministic", async () => {
    await expect(
      storeUploadInContext("space-1", {
        id: "att-2",
        filename: "pasted-image.png",
        content_type: "image/png",
        size_bytes: 100,
        url: "/uploads/files/pasted-image.png",
        uploaded_at: "2026-04-11T04:00:00.000Z",
      }),
    ).resolves.toBe("upload:1775880000000:pasted-image.png:att-2");

    expect(apiClient.post).toHaveBeenCalledWith(
      "/api/v1/context",
      expect.objectContaining({
        key: "upload:1775880000000:pasted-image.png:att-2",
      }),
    );
  });

  it("uses a unique context key for duplicate filenames", async () => {
    const first = buildUploadContextKey(
      { id: "attachment-one", filename: "image.png" },
      1775865960000,
    );
    const second = buildUploadContextKey(
      { id: "attachment-two", filename: "image.png" },
      1775865960000,
    );

    expect(first).toBe("upload:1775865960000:image.png:attachment-one");
    expect(second).toBe("upload:1775865960000:image.png:attachment-two");
    expect(first).not.toBe(second);
  });

  it("stores upload context under the generated upload key", async () => {
    await storeUploadInContext("space-1", {
      id: "att-1",
      filename: "test.png",
      content_type: "image/png",
      size_bytes: 100,
      url: "/uploads/files/test.png",
      uploaded_at: "2026-04-11T00:06:00.000Z",
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      "/api/v1/context",
      expect.objectContaining({
        key: "upload:1775865960000:test.png:att-1",
      }),
    );
  });
});

describe("sendSpaceAgentMessage", () => {
  it("sends forward metadata without attachments", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        message: {
          id: "message-1",
          content: "please review",
          created_at: "2026-04-17T05:00:00.000Z",
        },
      },
    });

    await sendSpaceAgentMessage("space-1", "please review", {
      metadata: {
        forward: {
          source_card_id: "card-1",
          source_message_id: "message-source",
          resource_uri: "ui://tasks/detail/task-1",
          title: "Task needs review",
        },
      },
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      "/api/v1/messages",
      expect.objectContaining({
        space_id: "space-1",
        content: "please review",
        metadata: {
          forward: {
            source_card_id: "card-1",
            source_message_id: "message-source",
            resource_uri: "ui://tasks/detail/task-1",
            title: "Task needs review",
          },
        },
      }),
    );
  });

  it("merges forward metadata with attachment metadata", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        message: {
          id: "message-2",
          content: "please review",
          created_at: "2026-04-17T05:00:00.000Z",
        },
      },
    });

    await sendSpaceAgentMessage("space-1", "please review", {
      metadata: {
        forward: {
          source_card_id: "card-2",
          title: "Context artifact",
        },
      },
      attachments: [
        {
          id: "attachment-1",
          filename: "notes.md",
          content_type: "text/markdown",
          size_bytes: 128,
          url: "/uploads/files/notes.md",
          context_key: "upload:notes.md",
        },
      ],
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      "/api/v1/messages",
      expect.objectContaining({
        attachments: [
          expect.objectContaining({
            id: "attachment-1",
            filename: "notes.md",
          }),
        ],
        metadata: expect.objectContaining({
          forward: {
            source_card_id: "card-2",
            title: "Context artifact",
          },
          attachments: [
            expect.objectContaining({
              id: "attachment-1",
              filename: "notes.md",
            }),
          ],
          accepted_attachments: [
            expect.objectContaining({
              id: "attachment-1",
              filename: "notes.md",
            }),
          ],
          context_uploads: [
            {
              key: "upload:notes.md",
              attachment_id: "attachment-1",
              filename: "notes.md",
              content_type: "text/markdown",
              url: "/uploads/files/notes.md",
            },
          ],
        }),
      }),
    );
  });
});

describe("sendSpaceAgentMessage receipt confirmation", () => {
  it("marks receipts unconfirmed when the server responds without any message id", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: {} });
    const receipt = await sendSpaceAgentMessage("space-1", "hello");
    expect(receipt.server_confirmed).toBe(false);
  });

  it("marks envelope receipts confirmed when the message carries a real id", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { message: { id: "m-1" } },
    });
    const receipt = await sendSpaceAgentMessage("space-1", "hello");
    expect(receipt.server_confirmed).toBe(true);
    expect(receipt.message_id).toBe("m-1");
  });

  it("marks flat receipts with an empty message_id unconfirmed", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { message_id: "" },
    });
    const receipt = await sendSpaceAgentMessage("space-1", "hello");
    expect(receipt.server_confirmed).toBe(false);
  });

  it("marks flat receipts with a message_id confirmed", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { message_id: "m-2" },
    });
    const receipt = await sendSpaceAgentMessage("space-1", "hello");
    expect(receipt.server_confirmed).toBe(true);
  });
});

describe("getSpaceAgentDirectory", () => {
  it("loads agents from the agents endpoint instead of user space members", async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        agents: [
          {
            id: "agent-1",
            name: "chatgpt",
            display_name: "ChatGPT",
            status: "active",
            origin: "mcp",
            specialization: "MCP widget testing",
            capabilities: ["messages", "agents"],
          },
        ],
      },
    });

    const result = await getSpaceAgentDirectory("space-1");

    expect(apiClient.get).toHaveBeenCalledWith(
      "/api/v1/agents?space_id=space-1&limit=200",
    );
    expect(result.members).toEqual([
      {
        id: "agent-1",
        handle: "chatgpt",
        display_name: "ChatGPT",
        active: true,
        status: "active",
        lifecycle_state: null,
        last_heartbeat: null,
        presence_fresh: null,
        presence_age_seconds: null,
        is_online: null,
        last_seen: null,
        last_heartbeat_at: null,
        last_active_at: null,
        presence_source: null,
        runtime_location: { kind: "mcp", label: "mcp" },
        capabilities: ["messages", "agents"],
        capability_summary: "MCP widget testing",
      },
    ]);
  });

  it("supports the items fallback response shape", async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          {
            agent_id: "agent-2",
            agent_name: "orion",
            status: "inactive",
            origin: "cli",
            bio: "Backend coordination",
            capabilities: ["backend"],
          },
        ],
      },
    });

    const result = await getSpaceAgentDirectory("space-2");

    expect(apiClient.get).toHaveBeenCalledWith(
      "/api/v1/agents?space_id=space-2&limit=200",
    );
    expect(result.members).toEqual([
      {
        id: "agent-2",
        handle: "orion",
        display_name: "orion",
        active: false,
        status: "inactive",
        lifecycle_state: null,
        last_heartbeat: null,
        presence_fresh: null,
        presence_age_seconds: null,
        is_online: null,
        last_seen: null,
        last_heartbeat_at: null,
        last_active_at: null,
        presence_source: null,
        runtime_location: { kind: "cli", label: "cli" },
        capabilities: ["backend"],
        capability_summary: "Backend coordination",
      },
    ]);
  });
});

// The switched-space token must replace the previous context for MCP and SSE.
describe("switchSpaceAgentSpace", () => {
  it("stores a backend-issued context token and notifies authenticated consumers", async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { new_token: "switched-context", space_name: "Team" } });
    const onRefresh = vi.fn();
    window.addEventListener("auth:token-refreshed", onRefresh);
    await switchSpaceAgentSpace("team-space");
    expect(apiClient.post).toHaveBeenCalledWith("/api/spaces/switch", { space_id: "team-space" });
    expect(storage.getUserToken()).toBe("switched-context");
    expect(onRefresh).toHaveBeenCalledOnce();
    window.removeEventListener("auth:token-refreshed", onRefresh);
    storage.clearTokens();
  });
});
