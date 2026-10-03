import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "@/test/utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SpaceAgentWidgetDescriptor } from "@/lib/space-agent-api";
import { applyThemePreference } from "@/lib/theme";

vi.mock("@/lib/space-agent-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/space-agent-api")>(
    "@/lib/space-agent-api",
  );
  return {
    ...actual,
    getSpaceAgentToolCall: vi.fn(async () => null),
    proxyMcpResourceRead: vi.fn(async () => ({ contents: [] })),
    proxyMcpToolCall: vi.fn(async () => ({
      content: [],
      structuredContent: { ok: true },
    })),
  };
});

import { getSpaceAgentToolCall, proxyMcpToolCall } from "@/lib/space-agent-api";
import { AxMcpAppWidget } from "./AxMcpAppWidget";

class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = [];

  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn(() => []);
  readonly root: Element | null = null;
  readonly rootMargin: string;
  readonly thresholds: ReadonlyArray<number>;

  constructor(
    private readonly callback: IntersectionObserverCallback,
    options?: IntersectionObserverInit,
  ) {
    this.rootMargin = options?.rootMargin || "0px";
    this.thresholds = Array.isArray(options?.threshold)
      ? options.threshold
      : [options?.threshold ?? 0];
    MockIntersectionObserver.instances.push(this);
  }

  trigger(isIntersecting: boolean) {
    this.callback(
      [
        {
          isIntersecting,
          intersectionRatio: isIntersecting ? 1 : 0,
          target: document.createElement("div"),
          boundingClientRect: {} as DOMRectReadOnly,
          intersectionRect: {} as DOMRectReadOnly,
          rootBounds: null,
          time: Date.now(),
        },
      ] as IntersectionObserverEntry[],
      this as unknown as IntersectionObserver,
    );
  }
}

const widget: SpaceAgentWidgetDescriptor = {
  kind: "mcp_app",
  tool_name: "tasks",
  lifecycle: "complete",
  title: "Task Detail",
  resource_url: "https://widgets.paxai.app/task-detail",
};

describe("AxMcpAppWidget", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockIntersectionObserver.instances = [];
    vi.mocked(getSpaceAgentToolCall).mockResolvedValue(null as never);
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver as never);
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      return window.setTimeout(() => cb(performance.now()), 0);
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      window.clearTimeout(id);
    });
    applyThemePreference("dark");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lazy-mounts the iframe and unloads it again after it leaves the viewport", async () => {
    const { rerender } = render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
      />,
    );

    expect(screen.queryByTestId("ax-mcp-widget-frame")).not.toBeInTheDocument();
    expect(
      screen.getByText("Scroll this app into view to load it."),
    ).toBeInTheDocument();

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("ax-mcp-widget-frame")).toHaveAttribute(
      "loading",
      "lazy",
    );

    await act(async () => {
      observer.trigger(false);
    });

    expect(screen.queryByTestId("ax-mcp-widget-frame")).not.toBeInTheDocument();
    expect(
      screen.getByText("Scroll this app into view to load it."),
    ).toBeInTheDocument();

    rerender(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          title: "Task Detail 2",
          resource_url: "https://widgets.paxai.app/task-detail-2",
        }}
      />,
    );

    expect(screen.queryByTestId("ax-mcp-widget-frame")).not.toBeInTheDocument();
    expect(
      screen.getByText("Scroll this app into view to load it."),
    ).toBeInTheDocument();

    await act(async () => {
      observer.trigger(true);
    });

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();
  });

  it("keeps a mounted inline widget open when the browser tab is hidden and restored", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
      />,
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    const iframe = await screen.findByTestId("ax-mcp-widget-frame");
    expect(iframe).toBeInTheDocument();

    const visibilitySpy = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(screen.getByTestId("ax-mcp-widget-frame")).toBe(iframe);

    visibilitySpy.mockReturnValue("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(screen.getByTestId("ax-mcp-widget-frame")).toBe(iframe);

    await act(async () => {
      observer.trigger(false);
    });

    expect(screen.queryByTestId("ax-mcp-widget-frame")).not.toBeInTheDocument();

    visibilitySpy.mockRestore();
  });

  it("does not show the bridge timeout while a mounted widget is backgrounded", async () => {
    vi.useFakeTimers();
    const visibilitySpy = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("visible");

    try {
      render(
        <AxMcpAppWidget
          messageId="message-1"
          spaceId="space-1"
          widget={widget}
        />,
      );

      const observer = MockIntersectionObserver.instances[0];
      expect(observer).toBeDefined();

      await act(async () => {
        observer.trigger(true);
      });

      const iframe = screen.getByTestId("ax-mcp-widget-frame");
      expect(iframe).toBeInTheDocument();

      visibilitySpy.mockReturnValue("hidden");
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
      });

      await act(async () => {
        vi.advanceTimersByTime(16_000);
      });

      expect(screen.getByTestId("ax-mcp-widget-frame")).toBe(iframe);
      expect(
        screen.queryByText("Widget stopped responding"),
      ).not.toBeInTheDocument();

      visibilitySpy.mockReturnValue("visible");
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
      });

      await act(async () => {
        vi.advanceTimersByTime(15_000);
      });

      expect(screen.getByText("Widget stopped responding")).toBeInTheDocument();
    } finally {
      visibilitySpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it("keeps force-mounted widgets alive even after the viewport observer says they are offscreen", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
        forceMount
      />,
    );

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("ax-mcp-widget")).toHaveAttribute(
      "data-quick-launch",
      "true",
    );
    expect(screen.getByTestId("ax-mcp-widget-frame")).toHaveAttribute(
      "loading",
      "eager",
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeUndefined();
  });

  it("lets the app panel own title and close chrome in panel mode", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
        forceMount
        panelMode
        onClose={vi.fn()}
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    expect(iframe).toBeInTheDocument();
    expect(screen.getByTestId("ax-mcp-widget")).toHaveClass(
      "flex",
      "h-full",
      "min-h-0",
      "flex-col",
    );
    expect(screen.getByTestId("ax-mcp-widget-frame-wrap")).toHaveClass(
      "h-full",
      "bg-slate-950",
    );
    expect(screen.getByTestId("ax-mcp-widget-frame-wrap")).not.toHaveClass(
      "rounded-[28px]",
    );
    expect(screen.getByTestId("ax-mcp-widget-frame-wrap")).not.toHaveClass(
      "border",
    );
    expect(screen.getByTestId("ax-mcp-widget-frame-wrap")).toHaveStyle({
      height: "100%",
    });
    expect(screen.queryByText("App panel")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /minimize/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /close widget/i })).toBeNull();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "initialize-panel-mode",
            method: "ui/initialize",
          },
        }),
      );
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          jsonrpc: "2.0",
          id: "initialize-panel-mode",
          result: expect.objectContaining({
            hostCapabilities: expect.objectContaining({
              sandbox: {
                csp: { resourceDomains: [window.location.origin] },
              },
            }),
            hostContext: expect.objectContaining({
              displayMode: "fullscreen",
              containerDimensions: expect.objectContaining({
                maxHeight: 720,
              }),
            }),
          }),
        }),
        "*",
      );
    });
  });

  it("preserves light-themed iframe surfaces in fullscreen panel mode", async () => {
    applyThemePreference("light");

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app">Light content</div></body></html>',
        }}
        forceMount
        panelMode
      />,
    );

    const iframe = await screen.findByTestId("ax-mcp-widget-frame");
    const frameWrap = screen.getByTestId("ax-mcp-widget-frame-wrap");

    expect(frameWrap).toHaveClass("h-full", "bg-white");
    expect(frameWrap).not.toHaveClass("bg-transparent");
    expect(iframe).toHaveClass("bg-white");
    expect(iframe).not.toHaveClass("bg-transparent");
  });

  it("passes the current app theme into ui/initialize without encoding it into frame-host src", async () => {
    applyThemePreference("light");

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");
    const frameUrl = new URL(
      iframe.getAttribute("src") || "",
      window.location.origin,
    );

    expect(frameUrl.pathname).toBe("/ax-mcp-frame-host.html");
    expect(frameUrl.searchParams.get("theme")).toBeNull();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "initialize-theme",
            method: "ui/initialize",
          },
        }),
      );
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          jsonrpc: "2.0",
          id: "initialize-theme",
          result: expect.objectContaining({
            hostContext: expect.objectContaining({
              theme: "light",
            }),
          }),
        }),
        "*",
      );
    });
  });

  it("updates frame-host iframes when the app theme changes without remounting them", async () => {
    applyThemePreference("light");

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const initialSrc = iframe.getAttribute("src");
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      fireEvent.load(iframe);
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledExactlyOnceWith({
        jsonrpc: "2.0",
        method: "ui/notifications/host-context-changed",
        params: { theme: "light" },
      }, "*");
    });

    await act(async () => {
      applyThemePreference("dark");
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenLastCalledWith({
        jsonrpc: "2.0",
        method: "ui/notifications/host-context-changed",
        params: { theme: "dark" },
      }, "*");
    });

    expect(postMessage).toHaveBeenCalledTimes(2);
    expect(postMessage.mock.calls.some(([payload]) => payload.type === "ax/theme")).toBe(false);

    await act(async () => {
      window.dispatchEvent(new MessageEvent("message", {
        source: frameWindow ?? undefined,
        data: { jsonrpc: "2.0", method: "ui/notifications/initialized" },
      }));
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
        jsonrpc: "2.0",
        method: "ui/notifications/host-context-changed",
        params: expect.objectContaining({ theme: "dark" }),
      }), "*");
    });

    expect(screen.getByTestId("ax-mcp-widget-frame")).toHaveAttribute(
      "src",
      initialSrc,
    );
  });

  it("holds host navigation commands until the iframe app announces ready", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-tasks"
        spaceId="space-1"
        widget={widget}
        forceMount
        panelMode
        hostCommand={{ type: "tasks/all", nonce: 42 }}
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      fireEvent.load(iframe);
    });

    expect(
      postMessage.mock.calls.some(
        ([payload]) =>
          typeof payload === "object" &&
          payload !== null &&
          "type" in payload &&
          payload.type === "ax/host-command",
      ),
    ).toBe(false);

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            method: "ui/notifications/initialized",
          },
        }),
      );
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        { type: "ax/host-command", command: "tasks/all", nonce: 42 },
        "*",
      );
    });
  });

  it("delivers the latest queued host navigation command after app readiness", async () => {
    const { rerender } = render(
      <AxMcpAppWidget
        messageId="launcher-tasks"
        spaceId="space-1"
        widget={widget}
        forceMount
        panelMode
        hostCommand={{ type: "tasks/home", nonce: 1 }}
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      fireEvent.load(iframe);
    });

    rerender(
      <AxMcpAppWidget
        messageId="launcher-tasks"
        spaceId="space-1"
        widget={widget}
        forceMount
        panelMode
        hostCommand={{ type: "tasks/all", nonce: 2 }}
      />,
    );

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            method: "ui/notifications/initialized",
          },
        }),
      );
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        { type: "ax/host-command", command: "tasks/all", nonce: 2 },
        "*",
      );
    });

    expect(postMessage).not.toHaveBeenCalledWith(
      { type: "ax/host-command", command: "tasks/home", nonce: 1 },
      "*",
    );
  });

  it("defers quick-action app iframe boot until the launcher tool result is ready", async () => {
    const quickActionWidget: SpaceAgentWidgetDescriptor = {
      ...widget,
      resource_url: undefined,
      html: '<!doctype html><html><body><div id="app"></div></body></html>',
      lifecycle: "working",
      tool_input: {
        action: "list",
      },
    };

    const { rerender } = render(
      <AxMcpAppWidget
        messageId="launcher-tasks"
        spaceId="space-1"
        widget={quickActionWidget}
        forceMount
        panelMode
        deferFrameUntilToolResult
      />,
    );

    expect(screen.getByTestId("ax-mcp-widget-deferred")).toBeInTheDocument();
    expect(screen.queryByTestId("ax-mcp-widget-frame")).not.toBeInTheDocument();

    rerender(
      <AxMcpAppWidget
        messageId="launcher-tasks"
        spaceId="space-1"
        widget={{
          ...quickActionWidget,
          lifecycle: "complete",
          tool_result: {
            kind: "task_collection",
            data: {
              tasks: [],
            },
          },
        }}
        forceMount
        panelMode
        deferFrameUntilToolResult
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    expect(screen.queryByTestId("ax-mcp-widget-deferred")).toBeNull();
    expect(iframe.name).toMatch(/^ax-mcp-html-v2:/);
    const payload = JSON.parse(
      decodeURIComponent(
        escape(window.atob(iframe.name.replace(/^ax-mcp-html-v2:/, ""))),
      ),
    );
    expect(payload.globals.toolOutput).toEqual({
      structuredContent: {
        kind: "task_collection",
        data: {
          tasks: [],
        },
      },
    });
  });

  it("allows quick-launch widgets to use the viewer token for normal reads", async () => {
    vi.mocked(proxyMcpToolCall).mockClear();
    render(
      <AxMcpAppWidget
        messageId="launcher-1"
        spaceId="space-1"
        widget={widget}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "launcher-list-1",
            method: "tools/call",
            params: {
              name: "agents",
              arguments: { action: "list" },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(proxyMcpToolCall).toHaveBeenCalledWith(
        "agents",
        expect.objectContaining({ action: "list" }),
        { spaceId: "space-1" },
      );
    });
  });

  it("blocks transcript widget tool calls for non-HITL actions", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
      />,
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "tool-call-1",
            method: "tools/call",
            params: {
              name: "agents",
              // "create" is not in the HITL allowlist, must be blocked.
              arguments: { action: "create", name: "blocked" },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          jsonrpc: "2.0",
          id: "tool-call-1",
          error: expect.objectContaining({
            code: -32001,
            message: expect.stringContaining(
              "needs a user-approved write flow",
            ),
          }),
        }),
        "*",
      );
    });

    expect(
      screen.queryByText(/needs a user-approved write flow/),
    ).not.toBeInTheDocument();
  });

  it.each([
    {
      label: "canonical whoami bootstrap",
      widgetToolName: "whoami.get",
      requestToolName: "whoami",
      requestArguments: { action: "get" },
    },
    {
      label: "alias request without action",
      widgetToolName: "whoami",
      requestToolName: "whoami.get",
      requestArguments: {},
    },
    {
      label: "auth alias request",
      widgetToolName: "auth.whoami",
      requestToolName: "auth.whoami",
      requestArguments: { action: "bootstrap" },
    },
    {
      label: "auth alias dotted request",
      widgetToolName: "auth.whoami",
      requestToolName: "auth.whoami.get",
      requestArguments: {},
    },
  ])(
    "answers transcript identity bootstraps from cached tool output for $label",
    async ({ widgetToolName, requestToolName, requestArguments }) => {
      vi.mocked(proxyMcpToolCall).mockClear();
      const toolResult = {
        kind: "whoami_profile",
        data: {
          identity: {
            handle: "aX",
          },
        },
      };

      render(
        <AxMcpAppWidget
          messageId="message-1"
          spaceId="space-1"
          widget={{
            ...widget,
            tool_name: widgetToolName,
            tool_result: toolResult,
          }}
        />,
      );

      const observer = MockIntersectionObserver.instances[0];
      expect(observer).toBeDefined();

      await act(async () => {
        observer.trigger(true);
      });

      const iframe = (await screen.findByTestId(
        "ax-mcp-widget-frame",
      )) as HTMLIFrameElement;
      const frameWindow = iframe.contentWindow;
      expect(frameWindow).toBeTruthy();

      const postMessage = vi.spyOn(frameWindow!, "postMessage");

      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            source: frameWindow ?? undefined,
            data: {
              jsonrpc: "2.0",
              id: `identity-bootstrap-${requestToolName}`,
              method: "tools/call",
              params: {
                name: requestToolName,
                arguments: requestArguments,
              },
            },
          }),
        );
      });

      await waitFor(() => {
        expect(postMessage).toHaveBeenCalledWith(
          expect.objectContaining({
            jsonrpc: "2.0",
            id: `identity-bootstrap-${requestToolName}`,
            result: {
              structuredContent: toolResult,
            },
          }),
          "*",
        );
      });

      expect(proxyMcpToolCall).not.toHaveBeenCalled();
    },
  );

  it("does not replay cached transcript identity output for whoami mutations", async () => {
    vi.mocked(proxyMcpToolCall).mockClear();
    const toolResult = {
      kind: "whoami_profile",
      data: {
        identity: {
          handle: "aX",
        },
      },
    };

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "whoami.get",
          tool_result: toolResult,
        }}
      />,
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "identity-update-1",
            method: "tools/call",
            params: {
              name: "whoami.update",
              arguments: { action: "update", bio: "should not fake success" },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          jsonrpc: "2.0",
          id: "identity-update-1",
          error: expect.objectContaining({
            code: -32001,
            message: expect.stringContaining(
              "needs a user-approved write flow",
            ),
          }),
        }),
        "*",
      );
    });

    expect(proxyMcpToolCall).not.toHaveBeenCalled();
  });

  it("allows transcript widget HITL finalization actions through", async () => {
    vi.mocked(proxyMcpToolCall).mockClear();
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
      />,
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "approve-1",
            method: "tools/call",
            params: {
              name: "agents",
              arguments: {
                action: "approve_draft",
                draft_id: "draft-1",
                version: 1,
              },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(proxyMcpToolCall).toHaveBeenCalledWith(
        "agents",
        expect.objectContaining({
          action: "approve_draft",
          draft_id: "draft-1",
          version: 1,
        }),
        { spaceId: "space-1" },
      );
    });
  });

  it("allows transcript widget draft edit actions through", async () => {
    vi.mocked(proxyMcpToolCall).mockClear();
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
      />,
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "edit-1",
            method: "tools/call",
            params: {
              name: "spaces",
              arguments: {
                action: "edit_draft",
                draft_id: "draft-1",
                version: 1,
                changes: { "space.name": "Updated" },
              },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(proxyMcpToolCall).toHaveBeenCalledWith(
        "spaces",
        expect.objectContaining({
          action: "edit_draft",
          draft_id: "draft-1",
        }),
        { spaceId: "space-1" },
      );
    });
  });

  it.each([
    ["agents", "list"],
    ["agents", "get"],
    ["spaces", "discover"],
    ["spaces", "members"],
    ["tasks", "list"],
  ])(
    "allows transcript %s %s read calls as viewer-local interactions",
    async (toolName, action) => {
      vi.mocked(proxyMcpToolCall).mockClear();
      render(
        <AxMcpAppWidget
          messageId="message-1"
          spaceId="space-1"
          widget={widget}
        />,
      );

      const observer = MockIntersectionObserver.instances[0];
      expect(observer).toBeDefined();

      await act(async () => {
        observer.trigger(true);
      });

      const iframe = (await screen.findByTestId(
        "ax-mcp-widget-frame",
      )) as HTMLIFrameElement;
      const frameWindow = iframe.contentWindow;
      expect(frameWindow).toBeTruthy();

      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            source: frameWindow ?? undefined,
            data: {
              jsonrpc: "2.0",
              id: `${toolName}-${action}-1`,
              method: "tools/call",
              params: {
                name: toolName,
                arguments: { action },
              },
            },
          }),
        );
      });

      await waitFor(() => {
        expect(proxyMcpToolCall).toHaveBeenCalledWith(
          toolName,
          { action },
          { spaceId: "space-1" },
        );
      });
    },
  );

  it.each([
    ["tasks", "create"],
    ["tasks", "update"],
    ["context", "set"],
    ["agents", "create"],
    ["spaces", "create"],
  ])(
    "blocks transcript %s %s write calls from using the viewer token",
    async (toolName, action) => {
      vi.mocked(proxyMcpToolCall).mockClear();
      render(
        <AxMcpAppWidget
          messageId="message-1"
          spaceId="space-1"
          widget={widget}
        />,
      );

      const observer = MockIntersectionObserver.instances[0];
      expect(observer).toBeDefined();

      await act(async () => {
        observer.trigger(true);
      });

      const iframe = (await screen.findByTestId(
        "ax-mcp-widget-frame",
      )) as HTMLIFrameElement;
      const frameWindow = iframe.contentWindow;
      expect(frameWindow).toBeTruthy();
      const postMessage = vi.spyOn(frameWindow!, "postMessage");

      await act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            source: frameWindow ?? undefined,
            data: {
              jsonrpc: "2.0",
              id: `${toolName}-${action}-1`,
              method: "tools/call",
              params: {
                name: toolName,
                arguments: { action },
              },
            },
          }),
        );
      });

      await waitFor(() => {
        expect(postMessage).toHaveBeenCalledWith(
          expect.objectContaining({
            jsonrpc: "2.0",
            id: `${toolName}-${action}-1`,
            error: expect.objectContaining({
              code: -32001,
              message: expect.stringContaining(
                "needs a user-approved write flow",
              ),
            }),
          }),
          "*",
        );
      });
      expect(proxyMcpToolCall).not.toHaveBeenCalled();
    },
  );

  it("blocks transcript draft actions without a draft_id", async () => {
    vi.mocked(proxyMcpToolCall).mockClear();
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
      />,
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "approve-without-draft",
            method: "tools/call",
            params: {
              name: "agents",
              arguments: { action: "approve_draft", version: 1 },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          jsonrpc: "2.0",
          id: "approve-without-draft",
          error: expect.objectContaining({
            code: -32001,
          }),
        }),
        "*",
      );
    });

    expect(proxyMcpToolCall).not.toHaveBeenCalled();
  });

  it("injects server-authored tool output into frame payloads before widget scripts run", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
          tool_result: {
            kind: "whoami_profile",
            data: {
              handle: "@aX",
            },
          },
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;

    expect(iframe.name).toMatch(/^ax-mcp-html-v2:/);
    const encoded = iframe.name.replace(/^ax-mcp-html-v2:/, "");
    const frameUrl = new URL(
      iframe.getAttribute("src") || "",
      window.location.origin,
    );
    const payloadKey = frameUrl.searchParams.get("payloadKey");
    const payload = JSON.parse(
      decodeURIComponent(escape(window.atob(encoded))),
    );

    expect(frameUrl.pathname).toBe("/ax-mcp-frame-host.html");
    expect(payloadKey).toBeTruthy();
    expect(window.sessionStorage.getItem(`ax-mcp-frame:${payloadKey}`)).toBe(
      iframe.name,
    );
    expect(payload.html).toContain('<div id="app"></div>');
    expect(payload.globals.toolOutput).toEqual({
      structuredContent: {
        kind: "whoami_profile",
        data: {
          handle: "@aX",
        },
      },
    });
  });

  it("defers oversized quick-action tool results instead of bloating the frame payload", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-context"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "context",
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="context"></div></body></html>',
          resource_uri: "ui://context/explorer",
          tool_result: {
            kind: "context",
            data: {
              items: [
                {
                  key: "huge-upload",
                  value: "x".repeat(700_000),
                },
              ],
            },
          },
        }}
        forceMount
        panelMode
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const encoded = iframe.name.replace(/^ax-mcp-html-v2:/, "");
    const payload = JSON.parse(
      decodeURIComponent(escape(window.atob(encoded))),
    );

    expect(payload.html).toContain('<div id="context"></div>');
    expect(payload.globals).toEqual({});

    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      fireEvent.load(iframe);
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "ax/openai:set_globals",
          globals: expect.objectContaining({
            toolOutput: expect.objectContaining({
              structuredContent: expect.objectContaining({
                kind: "context",
              }),
            }),
          }),
        }),
        "*",
      );
    });
  });

  it("keeps direct HTML fullscreen capability when oversized context results are deferred", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-context"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "context",
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="context"></div></body></html>',
          resource_uri: "ui://context/explorer",
          tool_result: {
            kind: "context",
            data: {
              items: [
                {
                  key: "missile-commander",
                  value: {
                    type: "html",
                    html: `<html><body>${"x".repeat(700_000)}</body></html>`,
                  },
                },
              ],
            },
          },
        }}
        forceMount
        panelMode
        onOpenDirectHtmlPanel={vi.fn()}
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const encoded = iframe.name.replace(/^ax-mcp-html-v2:/, "");
    const payload = JSON.parse(
      decodeURIComponent(escape(window.atob(encoded))),
    );

    expect(payload.html).toContain('<div id="context"></div>');
    expect(payload.globals).toEqual({
      axHostCapabilities: { directHtmlFullscreen: true },
    });
    expect(payload.globals.toolOutput).toBeUndefined();
  });

  it("does not mark direct HTML artifacts unresponsive when they do not speak the MCP app handshake", () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");

    render(
      <AxMcpAppWidget
        messageId="html-game"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "context",
          tool_action: "render_html",
          resource_uri: undefined,
          resource_url: undefined,
          html: '<!doctype html><html><body><canvas id="game"></canvas></body></html>',
        }}
        forceMount
        panelMode
      />,
    );

    expect(screen.getByTestId("ax-mcp-widget-frame")).toBeInTheDocument();

    expect(setTimeoutSpy).not.toHaveBeenCalledWith(
      expect.any(Function),
      15_000,
    );
    setTimeoutSpy.mockRestore();
  });

  it("opens direct HTML fullscreen when a widget promotes a selected HTML artifact", async () => {
    const onOpenDirectHtmlPanel = vi.fn();
    render(
      <AxMcpAppWidget
        messageId="launcher-context"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="context"></div></body></html>',
          resource_uri: "ui://context/explorer",
        }}
        forceMount
        panelMode
        onOpenDirectHtmlPanel={onOpenDirectHtmlPanel}
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "open-html-fullscreen",
            method: "ui/open-html-fullscreen",
            params: {
              title: "Tap Defense",
              key: "tap-defense",
              html: '<!doctype html><html><body><canvas id="game"></canvas></body></html>',
            },
          },
        }),
      );
    });

    expect(onOpenDirectHtmlPanel).toHaveBeenCalledWith({
      title: "Tap Defense",
      key: "tap-defense",
      html: '<!doctype html><html><body><canvas id="game"></canvas></body></html>',
    });
    expect(postMessage).toHaveBeenCalledWith(
      {
        jsonrpc: "2.0",
        id: "open-html-fullscreen",
        result: { ok: true },
      },
      "*",
    );
  });

  it("returns an error when direct HTML fullscreen is requested without a host callback", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-context"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="context"></div></body></html>',
          resource_uri: "ui://context/explorer",
        }}
        forceMount
        panelMode
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "open-html-fullscreen",
            method: "ui/open-html-fullscreen",
            params: {
              title: "Tap Defense",
              key: "tap-defense",
              html: '<!doctype html><html><body><canvas id="game"></canvas></body></html>',
            },
          },
        }),
      );
    });

    expect(postMessage).toHaveBeenCalledWith(
      {
        jsonrpc: "2.0",
        id: "open-html-fullscreen",
        error: {
          code: -32601,
          message: "Host does not support direct HTML fullscreen.",
        },
      },
      "*",
    );
  });

  it("replays the frame payload when the frame host cannot read sessionStorage", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-context"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="context"></div></body></html>',
          resource_uri: "ui://context/explorer",
        }}
        forceMount
        panelMode
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");
    const payloadKey = new URL(
      iframe.getAttribute("src") || "",
      window.location.origin,
    ).searchParams.get("payloadKey");

    expect(payloadKey).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            type: "ax/mcp-frame:request-payload",
            params: {
              payloadKey,
            },
          },
        }),
      );
    });

    expect(postMessage).toHaveBeenCalledWith(
      {
        type: "ax/mcp-frame:payload",
        payloadKey,
        payloadName: iframe.name,
      },
      "*",
    );
  });

  it("replays the current frame payload when the requested key matches", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-context"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="context"></div></body></html>',
          resource_uri: "ui://context/explorer",
        }}
        forceMount
        panelMode
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");
    const requestedKey = new URL(
      iframe.getAttribute("src") || "",
      window.location.origin,
    ).searchParams.get("payloadKey");

    expect(requestedKey).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            type: "ax/mcp-frame:request-payload",
            params: {
              payloadKey: requestedKey,
            },
          },
        }),
      );
    });

    expect(postMessage).toHaveBeenCalledWith(
      {
        type: "ax/mcp-frame:payload",
        payloadKey: requestedKey,
        payloadName: iframe.name,
      },
      "*",
    );
  });

  it("does not replay stored frame payloads for unrelated requested keys", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-context"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="context"></div></body></html>',
          resource_uri: "ui://context/explorer",
        }}
        forceMount
        panelMode
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");
    const unrelatedKey = "ax-mcp-frame-other-widget-key";

    window.sessionStorage.setItem(
      `ax-mcp-frame:${unrelatedKey}`,
      "ax-mcp-html-v2:other-widget-payload",
    );

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            type: "ax/mcp-frame:request-payload",
            params: {
              payloadKey: unrelatedKey,
            },
          },
        }),
      );
    });

    expect(postMessage).not.toHaveBeenCalled();
  });

  it("sends late tool input before late tool result", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_input: {
            action: "create_draft",
            name: "review_bot",
          },
          tool_result: {
            kind: "agent_collection",
            data: {
              scope: "create",
            },
          },
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();
    const postMessage = vi.spyOn(frameWindow!, "postMessage");

    await act(async () => {
      fireEvent.load(iframe);
    });

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          jsonrpc: "2.0",
          method: "ui/notifications/tool-result",
        }),
        "*",
      );
    });

    const jsonRpcPayloads = postMessage.mock.calls
      .map(([payload]) => payload)
      .filter(
        (payload): payload is { method?: string } =>
          Boolean(payload) &&
          typeof payload === "object" &&
          "jsonrpc" in payload,
      );
    const inputIndex = jsonRpcPayloads.findIndex(
      (payload) => payload.method === "ui/notifications/tool-input",
    );
    const resultIndex = jsonRpcPayloads.findIndex(
      (payload) => payload.method === "ui/notifications/tool-result",
    );

    expect(inputIndex).toBeGreaterThanOrEqual(0);
    expect(resultIndex).toBeGreaterThanOrEqual(0);
    expect(inputIndex).toBeLessThan(resultIndex);
  });

  it("normalizes snake_case structured_content before injecting tool output", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
          tool_result: {
            structured_content: {
              kind: "agent_collection",
              data: {
                scope: "create",
                draft: {
                  draft_id: "draft-1",
                  name: "review_bot",
                },
              },
            },
          } as unknown as SpaceAgentWidgetDescriptor["tool_result"],
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;

    expect(iframe.name).toMatch(/^ax-mcp-html-v2:/);
    const payload = JSON.parse(
      decodeURIComponent(
        escape(window.atob(iframe.name.replace(/^ax-mcp-html-v2:/, ""))),
      ),
    );

    expect(payload.globals.toolOutput).toEqual({
      structured_content: {
        kind: "agent_collection",
        data: {
          scope: "create",
          draft: {
            draft_id: "draft-1",
            name: "review_bot",
          },
        },
      },
      structuredContent: {
        kind: "agent_collection",
        data: {
          scope: "create",
          draft: {
            draft_id: "draft-1",
            name: "review_bot",
          },
        },
      },
    });
  });

  it("hydrates missing transcript tool output from the stored tool-call record", async () => {
    const toolCallId = "3d1c46da-3a7f-45a0-929d-4e73d4fe7f2f";
    vi.mocked(getSpaceAgentToolCall).mockResolvedValueOnce({
      tool_call_id: toolCallId,
      tool_name: "agents",
      status: "complete",
      initial_data: {
        kind: "agent_collection",
        data: {
          scope: "create",
          draft: {
            draft_id: "draft-2",
            name: "funny_jokester",
          },
        },
      },
    });

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "agents",
          tool_call_id: toolCallId,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;

    await waitFor(() => {
      expect(getSpaceAgentToolCall).toHaveBeenCalledWith(toolCallId);
      const payload = JSON.parse(
        decodeURIComponent(
          escape(window.atob(iframe.name.replace(/^ax-mcp-html-v2:/, ""))),
        ),
      );
      expect(payload.globals.toolOutput).toEqual({
        structuredContent: {
          kind: "agent_collection",
          data: {
            scope: "create",
            draft: {
              draft_id: "draft-2",
              name: "funny_jokester",
            },
          },
        },
      });
    });
  });

  it("retries stored tool-call hydration until durable initial data is available", async () => {
    const toolCallId = "2d48af44-11e6-433e-a78e-55ec0f41b0de";
    vi.mocked(getSpaceAgentToolCall)
      .mockResolvedValueOnce({
        tool_call_id: toolCallId,
        tool_name: "agents",
        status: "complete",
        initial_data: null,
      })
      .mockResolvedValueOnce({
        tool_call_id: toolCallId,
        tool_name: "agents",
        status: "complete",
        initial_data: {
          kind: "agent_collection",
          data: {
            scope: "create",
            draft: {
              draft_id: "draft-3",
              name: "orbit_builder",
            },
          },
        },
      });

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "agents",
          tool_call_id: toolCallId,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;

    await waitFor(() => {
      expect(
        vi.mocked(getSpaceAgentToolCall).mock.calls.length,
      ).toBeGreaterThanOrEqual(2);
      const payload = JSON.parse(
        decodeURIComponent(
          escape(window.atob(iframe.name.replace(/^ax-mcp-html-v2:/, ""))),
        ),
      );
      expect(payload.globals.toolOutput).toEqual({
        structuredContent: {
          kind: "agent_collection",
          data: {
            scope: "create",
            draft: {
              draft_id: "draft-3",
              name: "orbit_builder",
            },
          },
        },
      });
    });
  });

  it("keeps late tool output in the frame-host payload", async () => {
    const toolCallId = "3ac5236e-0f0b-44b4-889b-1a7f32f5f667";
    let resolveToolCall:
      | ((value: Awaited<ReturnType<typeof getSpaceAgentToolCall>>) => void)
      | null = null;

    vi.mocked(getSpaceAgentToolCall).mockReturnValue(
      new Promise((resolve) => {
        resolveToolCall = resolve;
      }) as ReturnType<typeof getSpaceAgentToolCall>,
    );

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "agents",
          tool_call_id: toolCallId,
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    await act(async () => {
      resolveToolCall?.({
        tool_call_id: toolCallId,
        tool_name: "agents",
        status: "complete",
        initial_data: {
          kind: "agent_collection",
          data: {
            scope: "create",
            draft: {
              draft_id: "draft-late",
              name: "late_bridge_probe",
            },
          },
        },
      });
    });

    await waitFor(() => {
      const payload = JSON.parse(
        decodeURIComponent(
          escape(window.atob(iframe.name.replace(/^ax-mcp-html-v2:/, ""))),
        ),
      );
      expect(payload.globals.toolOutput).toEqual({
        structuredContent: {
          kind: "agent_collection",
          data: {
            scope: "create",
            draft: {
              draft_id: "draft-late",
              name: "late_bridge_probe",
            },
          },
        },
      });
    });
  });

  it("does not hydrate synthetic launcher panel ids as backend tool calls", async () => {
    render(
      <AxMcpAppWidget
        messageId="launcher-agents"
        spaceId="space-1"
        widget={{
          ...widget,
          tool_name: "agents",
          tool_call_id: "mcp-panel-agents-1776022345239:tool",
          resource_url: undefined,
          html: '<!doctype html><html><body><div id="app"></div></body></html>',
        }}
        forceMount
      />,
    );

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();
    expect(getSpaceAgentToolCall).not.toHaveBeenCalled();
  });

  it("encodes raw HTML for the legacy frame-host fallback", async () => {
    const html =
      '<!doctype html><html><body><div id="app">fallback</div></body></html>';
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          ...widget,
          resource_url: undefined,
          html,
          tool_result: {
            kind: "bad_payload",
            data: {
              // JSON.stringify throws on BigInt, forcing the legacy HTML path.
              value: BigInt(1),
            },
          } as unknown as SpaceAgentWidgetDescriptor["tool_result"],
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;

    expect(iframe.name).toMatch(/^ax-mcp-html:/);
    const encoded = iframe.name.replace(/^ax-mcp-html:/, "");
    const frameUrl = new URL(
      iframe.getAttribute("src") || "",
      window.location.origin,
    );
    const payloadKey = frameUrl.searchParams.get("payloadKey");
    const decoded = decodeURIComponent(escape(window.atob(encoded)));

    expect(frameUrl.pathname).toBe("/ax-mcp-frame-host.html");
    expect(payloadKey).toBeTruthy();
    expect(window.sessionStorage.getItem(`ax-mcp-frame:${payloadKey}`)).toBe(
      iframe.name,
    );
    expect(decoded).toBe(html);
    expect(decoded).not.toBe(JSON.stringify(html));
  });

  it("parks launcher widgets only after they are far above while the user is near the bottom", async () => {
    const { container } = render(
      <>
        <div data-scroll-viewport="true">
          <AxMcpAppWidget
            messageId="message-1"
            spaceId="space-1"
            widget={widget}
            forceMount
          />
        </div>
        <div data-composer-root="true" />
      </>,
    );

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();

    const scrollViewport = container.querySelector(
      '[data-scroll-viewport="true"]',
    ) as HTMLDivElement | null;
    expect(scrollViewport).toBeTruthy();
    Object.defineProperty(scrollViewport!, "clientHeight", {
      configurable: true,
      value: 900,
    });
    Object.defineProperty(scrollViewport!, "scrollHeight", {
      configurable: true,
      value: 4000,
    });
    Object.defineProperty(scrollViewport!, "scrollTop", {
      configurable: true,
      writable: true,
      value: 1200,
    });
    vi.spyOn(scrollViewport!, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      bottom: 900,
      right: 1000,
      width: 1000,
      height: 900,
      toJSON: () => ({}),
    } as DOMRect);

    const composerRoot = container.querySelector(
      '[data-composer-root="true"]',
    ) as HTMLDivElement | null;
    expect(composerRoot).toBeTruthy();
    vi.spyOn(composerRoot!, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 780,
      top: 780,
      left: 0,
      bottom: 900,
      right: 1000,
      width: 1000,
      height: 120,
      toJSON: () => ({}),
    } as DOMRect);

    const shell = screen.getByTestId("ax-mcp-widget");
    const shellRectSpy = vi.spyOn(shell, "getBoundingClientRect");
    shellRectSpy.mockReturnValue({
      x: 0,
      y: -420,
      top: -420,
      left: 0,
      bottom: -120,
      right: 1000,
      width: 1000,
      height: 300,
      toJSON: () => ({}),
    } as DOMRect);

    await act(async () => {
      scrollViewport!.dispatchEvent(new Event("scroll"));
    });

    await waitFor(() => {
      expect(
        screen.queryByTestId("ax-mcp-widget-parked"),
      ).not.toBeInTheDocument();
      expect(screen.getByTestId("ax-mcp-widget-frame")).toBeInTheDocument();
    });

    shellRectSpy.mockReturnValue({
      x: 0,
      y: -1900,
      top: -1900,
      left: 0,
      bottom: -1600,
      right: 1000,
      width: 1000,
      height: 300,
      toJSON: () => ({}),
    } as DOMRect);

    await act(async () => {
      scrollViewport!.dispatchEvent(new Event("scroll"));
    });

    await waitFor(() => {
      expect(
        screen.queryByTestId("ax-mcp-widget-parked"),
      ).not.toBeInTheDocument();
      expect(screen.getByTestId("ax-mcp-widget-frame")).toBeInTheDocument();
    });

    scrollViewport!.scrollTop = 2840;

    await act(async () => {
      scrollViewport!.dispatchEvent(new Event("scroll"));
    });

    expect(
      await screen.findByTestId("ax-mcp-widget-parked"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("ax-mcp-widget-frame")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("ax-mcp-widget-parked"));

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();
  });

  it("unloads the iframe while a widget is minimized and restores it when reopened", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
      />,
    );

    const observer = MockIntersectionObserver.instances[0];
    expect(observer).toBeDefined();

    await act(async () => {
      observer.trigger(true);
    });

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Minimize widget" }),
    );

    expect(screen.queryByTestId("ax-mcp-widget-frame")).not.toBeInTheDocument();
    expect(screen.getByTestId("ax-mcp-widget-collapsed")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("ax-mcp-widget-collapsed"));

    expect(
      await screen.findByTestId("ax-mcp-widget-frame"),
    ).toBeInTheDocument();
  });

  it("renders close and minimize controls when dismiss is supported", async () => {
    const onClose = vi.fn();

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
        onClose={onClose}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Minimize widget" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close widget" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("hides transcript chrome controls inside the app panel", () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
        panelMode
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "Minimize widget" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Close widget" }),
    ).not.toBeInTheDocument();
  });

  it("renders Context Catalog trusted actions outside the panel iframe", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        panelMode
        forceMount
        widget={{
          kind: "mcp_app",
          tool_name: "context.get",
          lifecycle: "complete",
          title: "HTML Review Packet",
          html: "<button>Approve from sandbox</button>",
          initial_data: {
            context_catalog_entry: {
              id: "cat-html-review",
              title: "HTML Review Packet",
              artifact_type: "html.review",
              status: "active",
              current_context_object_id: "ctxobj-html-1",
              current_artifact_version_id: "artv-html-1",
              current_state_version_id: "stv-html-1",
              available_actions: [
                {
                  id: "approve",
                  canonical_label: "Approve",
                  label: "Approve from artifact",
                  source_lane: "trusted_named_action",
                  confirmation: "required",
                },
                {
                  id: "place_mark",
                  canonical_label: "Place mark",
                  source_lane: "in_world_proposal",
                },
              ],
            },
          },
        }}
      />,
    );

    const lane = screen.getByTestId("context-catalog-trusted-lane");
    const frameWrap = await screen.findByTestId("ax-mcp-widget-frame-wrap");

    expect(lane).toHaveTextContent("Artifact artv-html-1");
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByText("Approve from artifact")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Place mark" }),
    ).not.toBeInTheDocument();
    expect(
      lane.compareDocumentPosition(frameWrap) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders Context Catalog trusted actions from hydrated tool-call data", async () => {
    const toolCallId = "83b71c97-7ce5-42bb-93a3-038b8a11d7ec";
    vi.mocked(getSpaceAgentToolCall).mockResolvedValueOnce({
      tool_call_id: toolCallId,
      tool_name: "context.get",
      status: "complete",
      initial_data: {
        context_catalog_entry: {
          id: "cat-hydrated-review",
          title: "Hydrated Review Packet",
          artifact_type: "html.review",
          status: "active",
          current_context_object_id: "ctxobj-hydrated-1",
          current_artifact_version_id: "artv-hydrated-1",
          current_state_version_id: "stv-hydrated-1",
          available_actions: [
            {
              id: "approve",
              canonical_label: "Approve",
              label: "Approve from hydrated artifact",
              source_lane: "trusted_named_action",
              confirmation: "required",
            },
          ],
        },
      },
    });

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        panelMode
        forceMount
        widget={{
          kind: "mcp_app",
          tool_name: "context.get",
          tool_call_id: toolCallId,
          lifecycle: "complete",
          title: "Hydrated Review Packet",
          html: "<button>Approve from sandbox</button>",
        }}
      />,
    );

    const lane = await screen.findByTestId("context-catalog-trusted-lane");

    expect(getSpaceAgentToolCall).toHaveBeenCalledWith(toolCallId);
    expect(lane).toHaveTextContent("Hydrated Review Packet");
    expect(lane).toHaveTextContent("Artifact artv-hydrated-1");
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(
      screen.queryByText("Approve from hydrated artifact"),
    ).not.toBeInTheDocument();
  });

  it("renders model context updates as readable MCP context chips with previews", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          kind: "mcp_app",
          tool_name: "context.get",
          lifecycle: "complete",
          title: "Context",
          resource_url: "https://widgets.paxai.app/context",
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "context-preview-1",
            method: "ui/update-model-context",
            params: {
              context: {
                entries: [
                  {
                    title: "HTML review packet",
                    artifact_type: "html.review",
                    source: "Context Catalog",
                    preview:
                      "Review the latest sandboxed HTML artifact before approval.",
                  },
                  {
                    filename: "standup-notes.md",
                    file_type: "text/markdown",
                    tool_name: "context.get",
                    summary:
                      "Presence rollout notes and canary validation steps.",
                  },
                ],
              },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(
        screen.getAllByTestId("ax-mcp-widget-model-context-chip"),
      ).toHaveLength(2);
    });
    expect(screen.getByText("MCP context")).toBeInTheDocument();
    expect(screen.getByText("HTML review packet")).toBeInTheDocument();
    expect(screen.getByText("html.review")).toBeInTheDocument();
    expect(screen.getByText("Context Catalog")).toBeInTheDocument();
    expect(screen.getByText("standup-notes.md")).toBeInTheDocument();
    expect(
      screen.getByText("Presence rollout notes and canary validation steps."),
    ).toBeInTheDocument();
  });

  it("keeps distinct model context chips with matching titles keyed by preview", async () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});

    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          kind: "mcp_app",
          tool_name: "context.get",
          lifecycle: "complete",
          title: "Context",
          resource_url: "https://widgets.paxai.app/context",
        }}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;
    expect(frameWindow).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            id: "context-preview-duplicates",
            method: "ui/update-model-context",
            params: {
              context: {
                entries: [
                  {
                    title: "Review packet",
                    artifact_type: "html.review",
                    source: "Context Catalog",
                    preview: "First section preview",
                  },
                  {
                    title: "Review packet",
                    artifact_type: "html.review",
                    source: "Context Catalog",
                    preview: "Second section preview",
                  },
                ],
              },
            },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(
        screen.getAllByTestId("ax-mcp-widget-model-context-chip"),
      ).toHaveLength(2);
    });

    expect(screen.getByText("First section preview")).toBeInTheDocument();
    expect(screen.getByText("Second section preview")).toBeInTheDocument();
    expect(
      consoleError.mock.calls.some((call) =>
        call.some(
          (part) =>
            typeof part === "string" &&
            part.includes("Encountered two children with the same key"),
        ),
      ),
    ).toBe(false);
  });

  it("renders JSON fallback errors as friendly text", () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={{
          kind: "mcp_app",
          title: "Request processed",
          lifecycle: "error",
          fallback_text:
            '{"detail":{"code":"draft_invalid_payload","message":"Agent name must start with a letter and contain only letters, numbers, underscores, or hyphens"}}',
        }}
      />,
    );

    expect(
      screen.getByText(
        "Agent name must start with a letter and contain only letters, numbers, underscores, or hyphens",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/draft_invalid_payload/)).not.toBeInTheDocument();
  });

  it("keeps the outer widget shell in sync with iframe height changes", async () => {
    render(
      <AxMcpAppWidget
        messageId="message-1"
        spaceId="space-1"
        widget={widget}
        forceMount
      />,
    );

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const shell = screen.getByTestId("ax-mcp-widget");
    const frameWrap = screen.getByTestId("ax-mcp-widget-frame-wrap");
    const frameWindow = iframe.contentWindow;

    expect(frameWindow).toBeTruthy();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            method: "ui/notifications/size-changed",
            params: { height: 810 },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(frameWrap).toHaveStyle({ height: "810px" });
    });

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            method: "ui/notifications/size-changed",
            params: { height: 520 },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(frameWrap).toHaveStyle({ height: "520px" });
    });

    expect(shell).toContainElement(frameWrap);
  });

  it("does not auto-scroll launcher widgets after iframe height changes", async () => {
    const scrollTo = vi.fn();
    const { container } = render(
      <>
        <div data-scroll-viewport="true">
          <AxMcpAppWidget
            messageId="message-1"
            spaceId="space-1"
            widget={widget}
            forceMount
          />
        </div>
        <div data-composer-root="true" />
      </>,
    );

    const scrollViewport = container.querySelector(
      '[data-scroll-viewport="true"]',
    ) as HTMLDivElement | null;
    expect(scrollViewport).toBeTruthy();

    Object.defineProperty(scrollViewport!, "scrollTop", {
      value: 320,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(scrollViewport!, "scrollTo", {
      value: scrollTo,
      configurable: true,
    });

    const frameWrap = await screen.findByTestId("ax-mcp-widget-frame-wrap");

    const iframe = (await screen.findByTestId(
      "ax-mcp-widget-frame",
    )) as HTMLIFrameElement;
    const frameWindow = iframe.contentWindow;

    expect(frameWindow).toBeTruthy();

    await act(async () => {
      iframe.dispatchEvent(new Event("load"));
    });

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            method: "ui/notifications/initialized",
          },
        }),
      );
    });

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            method: "ui/notifications/size-changed",
            params: { height: 810 },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(frameWrap).toHaveStyle({
        height: "810px",
      });
    });
    expect(scrollTo).not.toHaveBeenCalled();

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frameWindow ?? undefined,
          data: {
            jsonrpc: "2.0",
            method: "ui/notifications/size-changed",
            params: { height: 920 },
          },
        }),
      );
    });

    await waitFor(() => {
      expect(frameWrap).toHaveStyle({
        height: "920px",
      });
    });
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
