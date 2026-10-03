import React, { ReactElement } from "react";
import { render, RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { storage } from "../lib/storage";
import { vi } from "vitest";
import { AuthProvider } from "../contexts/AuthContext";

// Mock the storage module
vi.mock("../lib/storage", () => ({
  storage: {
    getUserToken: vi.fn(() => "mock-token"),
    setUserToken: vi.fn(),
    clearUserToken: vi.fn(),
    getRefreshToken: vi.fn(() => "mock-refresh-token"),
    setRefreshToken: vi.fn(),
    setTokens: vi.fn(),
    setUsername: vi.fn(),
    clearTokens: vi.fn(),
    refreshTokens: vi.fn(() => Promise.resolve(true)),
    getUserTokenAsync: vi.fn(() => Promise.resolve("mock-token")),
    clearAll: vi.fn(),
  },
  migrateStorage: vi.fn(),
}));

interface AllTheProvidersProps {
  children: React.ReactNode;
  initialEntries?: string[];
}

const AllTheProviders = ({
  children,
  initialEntries = ["/"],
}: AllTheProvidersProps) => {
  // Create a new QueryClient for each test to avoid state leakage
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>{children}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

const customRender = (
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper"> & {
    initialEntries?: string[];
  },
) => {
  const { initialEntries, ...renderOptions } = options || {};

  return render(ui, {
    wrapper: ({ children }) => (
      <AllTheProviders initialEntries={initialEntries}>
        {children}
      </AllTheProviders>
    ),
    ...renderOptions,
  });
};

// Test data factories
export const createMockAgent = (overrides = {}) => ({
  id: "test-agent-1",
  agent_name: "Test Agent",
  description: "A test agent for unit testing",
  agent_type: "testing",
  status: "active",
  ...overrides,
});

export const createMockMcpConfig = (
  agentName = "test_agent",
  token = "mock-token",
) => ({
  mcpServers: {
    [`ax-marketplace-${agentName}`]: {
      command: "npx",
      args: [
        "-y",
        "mcp-remote@0.1.29",
        "--transport",
        "http-only",
        "--oauth-server",
        "http://localhost:8001",
        "--sse-path",
        "/mcp/sse",
        "--allow-http",
        "--header",
        `X-Agent-Name: ${agentName}`,
        "--debug",
        "http://localhost:8001/mcp/messages",
      ],
      env: {
        AGENT_NAME: agentName,
      },
    },
  },
});

export const createMockUser = (overrides = {}) => ({
  id: "test-user-1",
  username: "testuser",
  email: "test@example.com",
  full_name: "Test User",
  ...overrides,
});

// Helper to wait for loading states to resolve
export const waitForLoadingToFinish = () =>
  new Promise((resolve) => setTimeout(resolve, 0));

// Helper to simulate user authentication
export const mockAuthenticatedUser = () => {
  vi.mocked(storage.getUserToken).mockReturnValue("mock-auth-token");
  vi.mocked(storage.getRefreshToken).mockReturnValue("mock-refresh-token");
};

// Helper to simulate unauthenticated user
export const mockUnauthenticatedUser = () => {
  vi.mocked(storage.getUserToken).mockReturnValue(null);
  vi.mocked(storage.getRefreshToken).mockReturnValue(null);
};

// Re-export everything from React Testing Library
export * from "@testing-library/react";
export { default as userEvent } from "@testing-library/user-event";

// Override render method
export { customRender as render };
