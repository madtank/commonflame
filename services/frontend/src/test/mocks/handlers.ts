import { http, HttpResponse } from "msw";

// Auth validation helper for MSW handlers
const validateAuth = (request: Request) => {
  const authHeader = request.headers.get("authorization");
  if (!authHeader || !authHeader.startsWith("Bearer ")) {
    return {
      valid: false,
      error: { detail: "Missing or invalid authorization header" },
    };
  }

  const token = authHeader.replace("Bearer ", "");
  // Accept test tokens that match what our test setup uses
  const validTokens = [
    "mock-token",
    "mock-access-token",
    "mock-agent-token-123",
    "fresh-mock-token-456",
    "new-mock-token-789",
  ];

  if (!validTokens.includes(token)) {
    return {
      valid: false,
      error: { detail: "Could not validate credentials" },
    };
  }

  return { valid: true };
};

const buildMessagesListResponse = (request: Request) => {
  const url = new URL(request.url);
  const mediaType = url.searchParams.get("media_type");
  const before = url.searchParams.get("before");
  const limit = parseInt(url.searchParams.get("limit") || "50");

  // Test data with different media types
  const allMessages = [
    {
      id: "msg-1",
      content: "Test message with image ![alt](https://example.com/image.jpg)",
      username: "testuser",
      uploaded_at: "2025-07-21T10:00:00Z",
      created_at: "2025-07-21T10:00:00Z",
      channel: "main",
      agent_id: null,
    },
    {
      id: "msg-2",
      content: "Test message with video https://youtube.com/watch?v=abc123",
      username: "testuser",
      uploaded_at: "2025-07-21T09:00:00Z",
      created_at: "2025-07-21T09:00:00Z",
      channel: "main",
      agent_id: null,
    },
    {
      id: "msg-3",
      content: "Test message with audio https://example.com/song.mp3",
      username: "testuser",
      uploaded_at: "2025-07-21T08:00:00Z",
      created_at: "2025-07-21T08:00:00Z",
      channel: "main",
      agent_id: null,
    },
    {
      id: "msg-4",
      content: "Plain text message without media",
      username: "testuser",
      uploaded_at: "2025-07-21T07:00:00Z",
      created_at: "2025-07-21T07:00:00Z",
      channel: "main",
      agent_id: null,
      waiting_for_response: true,
      waiting_since: "2025-07-21T07:30:00Z",
      waiting_ttl_seconds: 180,
      waiting_for: "protocol_sage",
    },
    {
      id: "msg-5",
      content: "Older message for pagination test",
      username: "testuser",
      uploaded_at: "2025-07-20T10:00:00Z",
      created_at: "2025-07-20T10:00:00Z",
      channel: "main",
      agent_id: null,
    },
  ];

  // Filter by media type
  let filteredMessages = allMessages;
  if (mediaType && mediaType !== "all") {
    filteredMessages = allMessages.filter((msg) => {
      const content = msg.content.toLowerCase();
      switch (mediaType) {
        case "image":
          return (
            content.includes("![") ||
            content.includes(".jpg") ||
            content.includes(".png")
          );
        case "video":
          return (
            content.includes("youtube") ||
            content.includes("vimeo") ||
            content.includes(".mp4")
          );
        case "audio":
          return (
            content.includes(".mp3") ||
            content.includes(".wav") ||
            content.includes("soundcloud")
          );
        case "media":
          return (
            content.includes("![") ||
            content.includes(".jpg") ||
            content.includes(".png") ||
            content.includes("youtube") ||
            content.includes(".mp4") ||
            content.includes(".mp3") ||
            content.includes("soundcloud")
          );
        default:
          return true;
      }
    });
  }

  // Filter by before timestamp (pagination)
  if (before) {
    const beforeDate = new Date(before);
    filteredMessages = filteredMessages.filter(
      (msg) => new Date(msg.created_at) < beforeDate,
    );
  }

  // Apply limit
  const limitedMessages = filteredMessages.slice(0, limit);
  const hasMore = filteredMessages.length > limit;
  const oldestMsg = limitedMessages[limitedMessages.length - 1];

  return HttpResponse.json({
    posts: limitedMessages,
    messages: limitedMessages, // alias
    user_isolated: true,
    count: limitedMessages.length,
    has_more: hasMore,
    next_cursor: oldestMsg ? oldestMsg.created_at : null,
    oldest_timestamp: oldestMsg ? oldestMsg.created_at : null,
    latest_timestamp: limitedMessages[0]?.created_at || null,
  });
};

export const handlers = [
  http.get('/auth/local/invites', () => HttpResponse.json({ can_invite: false })),
  // Authentication endpoints
  http.post("/auth/login", async ({ request }) => {
    const body = (await request.json()) as any;

    if (body.email === "test@example.com" && body.password === "testpass123") {
      return HttpResponse.json({
        access_token: "mock-access-token",
        refresh_token: "mock-refresh-token",
        token_type: "bearer",
      });
    }

    return HttpResponse.json(
      { detail: "Invalid credentials" },
      { status: 401 },
    );
  }),

  http.post("/auth/register", async ({ request }) => {
    const body = (await request.json()) as any;

    return HttpResponse.json({
      access_token: "mock-access-token",
      refresh_token: "mock-refresh-token",
      token_type: "bearer",
    });
  }),

  // Agent management endpoints (path-only)
  http.get("/auth/agents", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      agents: [
        {
          id: "agent-1",
          agent_name: "Test Agent 1",
          description: "A test agent for unit testing",
          agent_type: "testing",
          status: "active",
        },
        {
          id: "agent-2",
          agent_name: "Test Agent 2",
          description: "Another test agent",
          agent_type: "general",
          status: "inactive",
        },
      ],
    });
  }),

  // Agent name availability check
  http.get("/auth/agents/check-name", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const url = new URL(request.url);
    const name = url.searchParams.get("name");

    if (!name) {
      return HttpResponse.json(
        { available: false, message: "Name is required" },
        { status: 400 },
      );
    }

    // Reserved names check
    const reserved = ["admin", "system", "root", "api", "mcp", "platform"];
    if (reserved.includes(name.toLowerCase())) {
      return HttpResponse.json({
        available: false,
        message: `'${name}' is a reserved name and cannot be used`,
      });
    }

    // Mock: names starting with "taken" are considered taken
    if (name.toLowerCase().startsWith("taken")) {
      return HttpResponse.json({
        available: false,
        message: "You already have an agent with this name",
      });
    }

    return HttpResponse.json({ available: true });
  }),

  http.post("/auth/agents/register", async ({ request }) => {
    const body = (await request.json()) as any;
    const baseUrl = "http://localhost:8001";
    const agentName = (body.agent_name || body.name || "test_agent")
      .toLowerCase()
      .replace(/\s+/g, "_");

    return HttpResponse.json({
      mcp_config: {
        mcpServers: {
          [`ax-marketplace-${agentName}`]: {
            command: "npx",
            args: [
              "-y",
              "mcp-remote@0.1.29",
              "--transport",
              "http-only",
              "--oauth-server",
              baseUrl,
              "--sse-path",
              "/mcp/sse",
              "--allow-http",
              "--header",
              `X-Agent-Name: ${agentName}`,
              "--debug",
              `${baseUrl}/mcp/messages`,
            ],
            env: {
              AGENT_NAME: agentName,
            },
          },
        },
      },
    });
  }),

  http.get("/auth/agents/:agentId/config", ({ params }) => {
    const { agentId } = params;
    const baseUrl = "http://localhost:8001";
    const agentName = "test_agent";

    return HttpResponse.json({
      agent_id: agentId,
      agent_name: "Test Agent",
      api_token: "fresh-mock-token-456",
      server_url: baseUrl,
      mcp_config: {
        mcpServers: {
          "ax-marketplace-test_agent": {
            command: "npx",
            args: [
              "-y",
              "mcp-remote@0.1.29",
              "--transport",
              "http-only",
              "--oauth-server",
              baseUrl,
              "--sse-path",
              "/mcp/sse",
              "--allow-http",
              "--header",
              `X-Agent-Name: ${agentName}`,
              "--debug",
              `${baseUrl}/mcp/messages`,
            ],
            env: {
              AGENT_NAME: agentName,
            },
          },
        },
      },
    });
  }),

  http.delete("/auth/agents/:agentId", ({ params, request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const { agentId } = params;

    return HttpResponse.json({
      message: "Agent deleted successfully",
      agent_id: agentId,
    });
  }),

  http.post("/auth/agents/:agentId/regenerate-token", ({ params, request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const { agentId } = params;

    return HttpResponse.json({
      message: "Token regenerated successfully",
      agent_id: agentId,
      api_token: "new-mock-token-789",
    });
  }),

  // Absolute URL variants to ensure matching with axios baseURL (127.0.0.1)
  http.get("http://127.0.0.1:8001/auth/agents", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      agents: [
        {
          id: "agent-1",
          agent_name: "Test Agent 1",
          description: "A test agent for unit testing",
          agent_type: "testing",
          status: "active",
        },
        {
          id: "agent-2",
          agent_name: "Test Agent 2",
          description: "Another test agent",
          agent_type: "general",
          status: "inactive",
        },
      ],
    });
  }),
  // localhost aliases for absolute URL requests
  http.get("http://localhost:8001/auth/agents", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      agents: [
        {
          id: "agent-1",
          agent_name: "Test Agent 1",
          description: "A test agent for unit testing",
          agent_type: "testing",
          status: "active",
        },
        {
          id: "agent-2",
          agent_name: "Test Agent 2",
          description: "Another test agent",
          agent_type: "general",
          status: "inactive",
        },
      ],
    });
  }),
  http.get("http://127.0.0.1:8001/auth/agents/check-name", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const url = new URL(request.url);
    const name = url.searchParams.get("name");

    if (!name) {
      return HttpResponse.json(
        { available: false, message: "Name is required" },
        { status: 400 },
      );
    }

    const reserved = ["admin", "system", "root", "api", "mcp", "platform"];
    if (reserved.includes(name.toLowerCase())) {
      return HttpResponse.json({
        available: false,
        message: `'${name}' is a reserved name and cannot be used`,
      });
    }

    if (name.toLowerCase().startsWith("taken")) {
      return HttpResponse.json({
        available: false,
        message: "You already have an agent with this name",
      });
    }

    return HttpResponse.json({ available: true });
  }),
  http.get("http://localhost:8001/auth/agents/check-name", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const url = new URL(request.url);
    const name = url.searchParams.get("name");

    if (!name) {
      return HttpResponse.json(
        { available: false, message: "Name is required" },
        { status: 400 },
      );
    }

    const reserved = ["admin", "system", "root", "api", "mcp", "platform"];
    if (reserved.includes(name.toLowerCase())) {
      return HttpResponse.json({
        available: false,
        message: `'${name}' is a reserved name and cannot be used`,
      });
    }

    if (name.toLowerCase().startsWith("taken")) {
      return HttpResponse.json({
        available: false,
        message: "You already have an agent with this name",
      });
    }

    return HttpResponse.json({ available: true });
  }),
  http.post(
    "http://127.0.0.1:8001/auth/agents/register",
    async ({ request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      const body = (await request.json()) as any;
      const baseUrl = "http://localhost:8001";
      const agentName = (body.agent_name || body.name || "test_agent")
        .toLowerCase()
        .replace(/\s+/g, "_");
      return HttpResponse.json({
        mcp_config: {
          mcpServers: {
            [`ax-marketplace-${agentName}`]: {
              command: "npx",
              args: [
                "-y",
                "mcp-remote@0.1.29",
                "--transport",
                "http-only",
                "--oauth-server",
                baseUrl,
                "--sse-path",
                "/mcp/sse",
                "--allow-http",
                "--header",
                `X-Agent-Name: ${agentName}`,
                "--debug",
                `${baseUrl}/mcp/messages`,
              ],
              env: {
                AGENT_NAME: agentName,
              },
            },
          },
        },
      });
    },
  ),
  http.post(
    "http://localhost:8001/auth/agents/register",
    async ({ request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      const body = (await request.json()) as any;
      const baseUrl = "http://localhost:8001";
      const agentName = (body.agent_name || body.name || "test_agent")
        .toLowerCase()
        .replace(/\s+/g, "_");
      return HttpResponse.json({
        mcp_config: {
          mcpServers: {
            [`ax-marketplace-${agentName}`]: {
              command: "npx",
              args: [
                "-y",
                "mcp-remote@0.1.29",
                "--transport",
                "http-only",
                "--oauth-server",
                baseUrl,
                "--sse-path",
                "/mcp/sse",
                "--allow-http",
                "--header",
                `X-Agent-Name: ${agentName}`,
                "--debug",
                `${baseUrl}/mcp/messages`,
              ],
              env: {
                AGENT_NAME: agentName,
              },
            },
          },
        },
      });
    },
  ),
  http.get(
    "http://127.0.0.1:8001/auth/agents/:agentId/config",
    ({ params, request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      const baseUrl = "http://localhost:8001";
      const agentName = "test_agent";
      return HttpResponse.json({
        agent_id: params.agentId,
        agent_name: "Test Agent",
        api_token: "fresh-mock-token-456",
        server_url: baseUrl,
        mcp_config: {
          mcpServers: {
            "ax-marketplace-test_agent": {
              command: "npx",
              args: [
                "-y",
                "mcp-remote@0.1.29",
                "--transport",
                "http-only",
                "--oauth-server",
                baseUrl,
                "--sse-path",
                "/mcp/sse",
                "--allow-http",
                "--header",
                `X-Agent-Name: ${agentName}`,
                "--debug",
                `${baseUrl}/mcp/messages`,
              ],
              env: {
                AGENT_NAME: agentName,
              },
            },
          },
        },
      });
    },
  ),
  http.get(
    "http://localhost:8001/auth/agents/:agentId/config",
    ({ params, request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      const baseUrl = "http://localhost:8001";
      const agentName = "test_agent";
      return HttpResponse.json({
        agent_id: params.agentId,
        agent_name: "Test Agent",
        api_token: "fresh-mock-token-456",
        server_url: baseUrl,
        mcp_config: {
          mcpServers: {
            "ax-marketplace-test_agent": {
              command: "npx",
              args: [
                "-y",
                "mcp-remote@0.1.29",
                "--transport",
                "http-only",
                "--oauth-server",
                baseUrl,
                "--sse-path",
                "/mcp/sse",
                "--allow-http",
                "--header",
                `X-Agent-Name: ${agentName}`,
                "--debug",
                `${baseUrl}/mcp/messages`,
              ],
              env: {
                AGENT_NAME: agentName,
              },
            },
          },
        },
      });
    },
  ),
  http.delete(
    "http://127.0.0.1:8001/auth/agents/:agentId",
    ({ params, request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      return HttpResponse.json({
        message: "Agent deleted successfully",
        agent_id: params.agentId,
      });
    },
  ),
  http.delete(
    "http://localhost:8001/auth/agents/:agentId",
    ({ params, request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      return HttpResponse.json({
        message: "Agent deleted successfully",
        agent_id: params.agentId,
      });
    },
  ),
  http.post(
    "http://127.0.0.1:8001/auth/agents/:agentId/regenerate-token",
    ({ params, request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      return HttpResponse.json({
        message: "Token regenerated successfully",
        agent_id: params.agentId,
        api_token: "new-mock-token-789",
      });
    },
  ),
  http.post(
    "http://localhost:8001/auth/agents/:agentId/regenerate-token",
    ({ params, request }) => {
      const auth = validateAuth(request);
      if (!auth.valid) {
        return HttpResponse.json(auth.error, { status: 401 });
      }
      return HttpResponse.json({
        message: "Token regenerated successfully",
        agent_id: params.agentId,
        api_token: "new-mock-token-789",
      });
    },
  ),

  // User endpoints
  http.get("/auth/me", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      id: "user-1",
      username: "testuser",
      email: "test@example.com",
      full_name: "Test User",
    });
  }),

  // Messages endpoints with media filtering and pagination support
  http.get("/auth/messages", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }
    return buildMessagesListResponse(request);
  }),

  http.get("/api/messages", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return buildMessagesListResponse(request);
  }),

  http.post("/auth/messages", async ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const body = (await request.json()) as any;

    return HttpResponse.json({
      id: "new-msg-1",
      content: body.content,
      username: "testuser",
      uploaded_at: new Date().toISOString(),
      channel: body.channel || "main",
    });
  }),

  // Health check
  http.get("/health", () => {
    return HttpResponse.json({
      status: "healthy",
      timestamp: new Date().toISOString(),
    });
  }),

  // Tasks endpoints
  http.get("/tasks", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      tasks: [
        {
          id: "task-1",
          title: "Test Task 1",
          description: "Description for test task 1",
          status: "pending",
          priority: "high",
          assigned_to: null,
          created_at: "2025-07-21T10:00:00Z",
          updated_at: "2025-07-21T10:00:00Z",
          organization_id: "org-1",
        },
        {
          id: "task-2",
          title: "Test Task 2",
          description: "Description for test task 2",
          status: "in_progress",
          priority: "medium",
          assigned_to: "user-1",
          created_at: "2025-07-21T11:00:00Z",
          updated_at: "2025-07-21T12:00:00Z",
          organization_id: "org-1",
        },
      ],
      total: 2,
    });
  }),

  http.post("/tasks", async ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const body = (await request.json()) as any;
    return HttpResponse.json({
      id: "new-task-1",
      title: body.title,
      description: body.description,
      status: "pending",
      priority: body.priority || "medium",
      assigned_to: body.assigned_to || null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      organization_id: "org-1",
    });
  }),

  http.put("/tasks/:taskId", async ({ request, params }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const body = (await request.json()) as any;
    return HttpResponse.json({
      id: params.taskId,
      ...body,
      updated_at: new Date().toISOString(),
    });
  }),

  http.patch("/tasks/:taskId", async ({ request, params }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const body = (await request.json()) as any;
    return HttpResponse.json({
      id: params.taskId,
      ...body,
      updated_at: new Date().toISOString(),
    });
  }),

  http.delete("/tasks/:taskId", ({ request, params }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      message: "Task deleted successfully",
      task_id: params.taskId,
    });
  }),
  http.put(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  http.patch(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  http.put(
    "http://127.0.0.1:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  http.patch(
    "http://127.0.0.1:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  // Task notes endpoints

  http.put(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  http.patch(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  // Task notes endpoints

  // API-prefixed task endpoints
  http.put(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  http.patch(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  // Task notes endpoints
  http.get("/tasks/:taskId/notes", ({ params }) => {
    return HttpResponse.json({
      notes: [
        {
          id: "note-1",
          task_id: params.taskId,
          content: "Test note 1",
          created_by: "user-1",
          created_at: "2025-07-21T10:00:00Z",
        },
      ],
    });
  }),

  // API-prefixed task notes endpoints (alias for /api/* requests)
  http.get("/api/v1/tasks/:taskId/notes", ({ params }) => {
    return HttpResponse.json({
      notes: [
        {
          id: "note-1",
          task_id: params.taskId,
          content: "Test note 1",
          created_by: "user-1",
          created_at: "2025-07-21T10:00:00Z",
        },
      ],
    });
  }),
  http.get("http://localhost:8001/api/v1/tasks/:taskId/notes", ({ params }) => {
    return HttpResponse.json({
      notes: [
        {
          id: "note-1",
          task_id: params.taskId,
          content: "Test note 1",
          created_by: "user-1",
          created_at: "2025-07-21T10:00:00Z",
        },
      ],
    });
  }),

  http.post("/tasks/:taskId/notes", async ({ request, params }) => {
    const body = (await request.json()) as any;
    return HttpResponse.json({
      id: "new-note-1",
      task_id: params.taskId,
      content: body.content,
      created_by: "user-1",
      created_at: new Date().toISOString(),
    });
  }),

  http.put(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  http.patch(
    "http://localhost:8001/api/v1/tasks/:taskId",
    async ({ request, params }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        id: params.taskId,
        ...body,
        updated_at: new Date().toISOString(),
      });
    },
  ),

  // Organization endpoints
  http.get("/organizations", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),

  // API-prefixed organization endpoints (alias for legacy /api/* requests in tests)
  http.get("/api/organizations", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),

  // Trailing slash variants for organizations (some code calls /api/organizations/)
  http.get("/api/organizations/", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),

  http.post("/organizations/switch", async ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const body = (await request.json()) as any;
    return HttpResponse.json({
      message: "Organization switched successfully",
      organization_id: body.organization_id,
      organization_name: `Test Organization ${body.organization_id.split("-")[1]}`,
    });
  }),

  http.get("/organizations/:orgId", ({ request, params }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    return HttpResponse.json({
      id: params.orgId,
      name: `Test Organization ${params.orgId}`,
      description: "Test organization description",
      created_at: "2025-07-01T10:00:00Z",
      member_count: 5,
      agent_count: 3,
    });
  }),

  // GitHub OAuth endpoints
  http.get("/auth/github", () => {
    // Simulate redirect to GitHub
    return new HttpResponse(null, {
      status: 302,
      headers: {
        Location:
          "https://github.com/login/oauth/authorize?client_id=test&redirect_uri=http://localhost:8001/auth/callback",
      },
    });
  }),

  http.get("/auth/callback", ({ request }) => {
    const url = new URL(request.url);
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");

    if (!code || !state) {
      return HttpResponse.json(
        { detail: "Invalid OAuth callback parameters" },
        { status: 400 },
      );
    }

    // Simulate redirect back to frontend with token
    return new HttpResponse(null, {
      status: 302,
      headers: {
        Location: `http://localhost:3000?token=mock-jwt-token&username=testuser`,
      },
    });
  }),

  // Token refresh endpoint
  http.post("/auth/refresh", async ({ request }) => {
    const body = (await request.json()) as any;

    if (body.refresh_token === "mock-refresh-token") {
      return HttpResponse.json({
        access_token: "new-mock-access-token",
        refresh_token: "new-mock-refresh-token",
        token_type: "bearer",
      });
    }

    return HttpResponse.json(
      { detail: "Invalid refresh token" },
      { status: 401 },
    );
  }),

  // Agent statistics endpoint
  http.get("/agents/:agentId/stats", ({ params }) => {
    return HttpResponse.json({
      agent_id: params.agentId,
      total_tasks: 42,
      completed_tasks: 35,
      active_tasks: 7,
      messages_sent: 156,
      last_active: "2025-07-21T15:30:00Z",
      health_status: "healthy",
      uptime_hours: 168,
    });
  }),

  // Search endpoint
  http.get("/api/search", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }
    const url = new URL(request.url);
    const scope = url.searchParams.get("scope") || "all";
    const scopeMap: Record<string, string> = {
      messages: "message",
      tasks: "task",
      agents: "agent",
    };
    const requestedType = scopeMap[scope] || "all";

    const results = [
      {
        type: "message",
        id: "msg-1",
        content: "Investigating Redis timeout in SSE pipeline",
        author: "testuser",
        channel: "main",
        timestamp: "2025-07-21T10:00:00Z",
        score: 0.92,
      },
      {
        type: "task",
        id: "task-1",
        title: "Fix auth flow retry handling",
        status: "in_progress",
        priority: "high",
        updated_at: "2025-07-21T11:00:00Z",
        score: 0.87,
      },
      {
        type: "agent",
        id: "agent-1",
        name: "protocol_sage",
        description: "FastAPI and Postgres optimization",
        specialization: "backend",
        score: 0.85,
      },
    ];

    const filtered =
      requestedType === "all"
        ? results
        : results.filter((result) => result.type === requestedType);

    return HttpResponse.json({
      results: filtered,
      total: filtered.length,
      query: url.searchParams.get("q") || "",
      trends: ["Redis issues", "Auth flow", "SSE events"],
    });
  }),
  http.get("http://127.0.0.1:8001/api/search", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }
    const url = new URL(request.url);
    const scope = url.searchParams.get("scope") || "all";
    const scopeMap: Record<string, string> = {
      messages: "message",
      tasks: "task",
      agents: "agent",
    };
    const requestedType = scopeMap[scope] || "all";

    const results = [
      {
        type: "message",
        id: "msg-1",
        content: "Investigating Redis timeout in SSE pipeline",
        author: "testuser",
        channel: "main",
        timestamp: "2025-07-21T10:00:00Z",
        score: 0.92,
      },
      {
        type: "task",
        id: "task-1",
        title: "Fix auth flow retry handling",
        status: "in_progress",
        priority: "high",
        updated_at: "2025-07-21T11:00:00Z",
        score: 0.87,
      },
      {
        type: "agent",
        id: "agent-1",
        name: "protocol_sage",
        description: "FastAPI and Postgres optimization",
        specialization: "backend",
        score: 0.85,
      },
    ];

    const filtered =
      requestedType === "all"
        ? results
        : results.filter((result) => result.type === requestedType);

    return HttpResponse.json({
      results: filtered,
      total: filtered.length,
      query: url.searchParams.get("q") || "",
      trends: ["Redis issues", "Auth flow", "SSE events"],
    });
  }),
  http.get("http://localhost:8001/api/search", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }
    const url = new URL(request.url);
    const scope = url.searchParams.get("scope") || "all";
    const scopeMap: Record<string, string> = {
      messages: "message",
      tasks: "task",
      agents: "agent",
    };
    const requestedType = scopeMap[scope] || "all";

    const results = [
      {
        type: "message",
        id: "msg-1",
        content: "Investigating Redis timeout in SSE pipeline",
        author: "testuser",
        channel: "main",
        timestamp: "2025-07-21T10:00:00Z",
        score: 0.92,
      },
      {
        type: "task",
        id: "task-1",
        title: "Fix auth flow retry handling",
        status: "in_progress",
        priority: "high",
        updated_at: "2025-07-21T11:00:00Z",
        score: 0.87,
      },
      {
        type: "agent",
        id: "agent-1",
        name: "protocol_sage",
        description: "FastAPI and Postgres optimization",
        specialization: "backend",
        score: 0.85,
      },
    ];

    const filtered =
      requestedType === "all"
        ? results
        : results.filter((result) => result.type === requestedType);

    return HttpResponse.json({
      results: filtered,
      total: filtered.length,
      query: url.searchParams.get("q") || "",
      trends: ["Redis issues", "Auth flow", "SSE events"],
    });
  }),
  http.get("/search", ({ request }) => {
    const auth = validateAuth(request);
    if (!auth.valid) {
      return HttpResponse.json(auth.error, { status: 401 });
    }

    const url = new URL(request.url);
    const query = url.searchParams.get("q");

    return HttpResponse.json({
      results: {
        tasks: [
          {
            id: "task-1",
            title: `Task matching "${query}"`,
            type: "task",
          },
        ],
        messages: [
          {
            id: "msg-1",
            content: `Message containing ${query}`,
            type: "message",
          },
        ],
        agents: [
          {
            id: "agent-1",
            name: `Agent ${query}`,
            type: "agent",
          },
        ],
      },
      total: 3,
    });
  }),

  // Absolute URL variants for axios baseURL (127.0.0.1:8001)
  http.get("http://127.0.0.1:8001/tasks", () => {
    return HttpResponse.json({
      tasks: [
        {
          id: "task-1",
          title: "Test Task 1",
          description: "Description for test task 1",
          status: "pending",
          priority: "high",
          assigned_to: null,
          created_at: "2025-07-21T10:00:00Z",
          updated_at: "2025-07-21T10:00:00Z",
          organization_id: "org-1",
        },
        {
          id: "task-2",
          title: "Test Task 2",
          description: "Description for test task 2",
          status: "in_progress",
          priority: "medium",
          assigned_to: "user-1",
          created_at: "2025-07-21T11:00:00Z",
          updated_at: "2025-07-21T12:00:00Z",
          organization_id: "org-1",
        },
      ],
      total: 2,
    });
  }),

  http.post("http://127.0.0.1:8001/tasks", async ({ request }) => {
    const body = (await request.json()) as any;
    return HttpResponse.json({
      id: "new-task-1",
      title: body.title,
      description: body.description,
      status: "pending",
      priority: body.priority || "medium",
      assigned_to: body.assigned_to || null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      organization_id: "org-1",
    });
  }),

  http.get("http://127.0.0.1:8001/organizations", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),
  http.get("http://localhost:8001/organizations", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),
  // Absolute URL trailing-slash variants
  http.get("http://127.0.0.1:8001/api/organizations/", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),
  http.get("http://localhost:8001/api/organizations/", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),
  // Absolute URL non-trailing variants for organizations
  http.get("http://127.0.0.1:8001/api/organizations", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),
  http.get("http://localhost:8001/api/organizations", () => {
    return HttpResponse.json({
      organizations: [
        {
          id: "org-1",
          name: "Test Organization 1",
          description: "First test organization",
          created_at: "2025-07-01T10:00:00Z",
        },
        {
          id: "org-2",
          name: "Test Organization 2",
          description: "Second test organization",
          created_at: "2025-07-01T11:00:00Z",
        },
        {
          id: "org-3",
          name: "Test Organization 3",
          description: "Third test organization",
          created_at: "2025-07-01T12:00:00Z",
        },
      ],
    });
  }),

  http.post(
    "http://127.0.0.1:8001/organizations/switch",
    async ({ request }) => {
      const body = (await request.json()) as any;
      return HttpResponse.json({
        message: "Organization switched successfully",
        organization_id: body.organization_id,
        organization_name: `Test Organization ${body.organization_id.split("-")[1]}`,
      });
    },
  ),

  http.get("http://127.0.0.1:8001/auth/me", () => {
    return HttpResponse.json({
      id: "user-1",
      username: "testuser",
      email: "test@example.com",
      full_name: "Test User",
      current_org_id: "org-1",
    });
  }),

  http.post("http://127.0.0.1:8001/auth/refresh", async ({ request }) => {
    const body = (await request.json()) as any;
    if (body.refresh_token === "mock-refresh-token") {
      return HttpResponse.json({
        access_token: "new-mock-access-token",
        refresh_token: "new-mock-refresh-token",
        token_type: "bearer",
      });
    }
    return HttpResponse.json(
      { detail: "Invalid refresh token" },
      { status: 401 },
    );
  }),

  http.get("http://127.0.0.1:8001/health", () => {
    return HttpResponse.json({
      status: "healthy",
      timestamp: new Date().toISOString(),
    });
  }),

  // Add absolute URL handlers for auth endpoints
  http.post("http://127.0.0.1:8001/auth/login", async ({ request }) => {
    const body = (await request.json()) as any;

    if (body.email === "test@example.com" && body.password === "testpass123") {
      return HttpResponse.json({
        access_token: "mock-access-token",
        refresh_token: "mock-refresh-token",
        token_type: "bearer",
      });
    }

    return HttpResponse.json(
      { detail: "Invalid credentials" },
      { status: 401 },
    );
  }),

  http.post("http://127.0.0.1:8001/auth/register", async ({ request }) => {
    const body = (await request.json()) as any;

    return HttpResponse.json({
      access_token: "mock-access-token",
      refresh_token: "mock-refresh-token",
      token_type: "bearer",
    });
  }),

  http.get("http://127.0.0.1:8001/auth/messages", ({ request }) => {
    return buildMessagesListResponse(request);
  }),

  http.get("http://127.0.0.1:8001/api/messages", ({ request }) => {
    return buildMessagesListResponse(request);
  }),

  http.post("http://127.0.0.1:8001/auth/messages", async ({ request }) => {
    const body = (await request.json()) as any;

    return HttpResponse.json({
      id: "new-msg-1",
      content: body.content,
      username: "testuser",
      uploaded_at: new Date().toISOString(),
      channel: body.channel || "main",
    });
  }),

  http.get("http://localhost:8001/auth/messages", ({ request }) => {
    return buildMessagesListResponse(request);
  }),

  http.get("http://localhost:8001/api/messages", ({ request }) => {
    return buildMessagesListResponse(request);
  }),

  http.post("http://localhost:8001/auth/messages", async ({ request }) => {
    const body = (await request.json()) as any;

    return HttpResponse.json({
      id: "new-msg-1",
      content: body.content,
      username: "testuser",
      uploaded_at: new Date().toISOString(),
      channel: body.channel || "main",
    });
  }),

  http.get("http://127.0.0.1:8001/search", ({ request }) => {
    const url = new URL(request.url);
    const query = url.searchParams.get("q");

    return HttpResponse.json({
      results: {
        tasks: [
          {
            id: "task-1",
            title: `Task matching "${query}"`,
            type: "task",
          },
        ],
        messages: [
          {
            id: "msg-1",
            content: `Message containing ${query}`,
            type: "message",
          },
        ],
        agents: [
          {
            id: "agent-1",
            name: `Agent ${query}`,
            type: "agent",
          },
        ],
      },
      total: 3,
    });
  }),

  // Fallback handler for unhandled requests
  http.all("*", ({ request }) => {
    console.warn(
      `Unhandled ${request.method} request to ${request.url}. ` +
        "Consider adding a request handler for this endpoint.",
    );
    return HttpResponse.json({ error: "Endpoint not mocked" }, { status: 404 });
  }),
];
