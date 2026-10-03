import { describe, expect, it, vi } from "vitest";
import {
  postWidgetFrameMessage,
  setWidgetOpenAiGlobals,
  setWidgetFrameTheme,
  type WidgetFrameWindow,
} from "./widget-frame-bridge";

describe("widget-frame-bridge", () => {
  it("updates known frame-host themes without sending raw messages to MCP listeners", () => {
    const frameWindow = {
      postMessage: vi.fn(),
      __axSetTheme: vi.fn(),
    };

    expect(setWidgetFrameTheme(frameWindow, "light")).toBe("direct");
    expect(frameWindow.__axSetTheme).toHaveBeenCalledWith("light");
    expect(frameWindow.postMessage).not.toHaveBeenCalled();
  });

  it.each([false, true])("uses a valid MCP theme notification when direct access is blocked=%s", (blocked) => {
    const frameWindow: WidgetFrameWindow = { postMessage: vi.fn() };
    if (blocked) {
      Object.defineProperty(frameWindow, "__axSetTheme", {
        get() { throw new DOMException("Cross-origin frame", "SecurityError"); },
      });
    }

    expect(setWidgetFrameTheme(frameWindow, "dark")).toBe("postMessage");
    expect(frameWindow.postMessage).toHaveBeenCalledExactlyOnceWith({
      jsonrpc: "2.0",
      method: "ui/notifications/host-context-changed",
      params: { theme: "dark" },
    }, "*");
  });

  it("prefers direct globals injection when the frame host exposes it", () => {
    const postMessage = vi.fn();
    const setGlobals = vi.fn();
    const frameWindow: WidgetFrameWindow = {
      postMessage,
      __axSetOpenAiGlobals: setGlobals,
    };

    expect(
      setWidgetOpenAiGlobals(frameWindow, { toolOutput: { ok: true } }),
    ).toBe("direct");
    expect(setGlobals).toHaveBeenCalledWith({ toolOutput: { ok: true } });
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("posts globals injection when the frame host does not expose a direct hook", () => {
    const postMessage = vi.fn();
    const frameWindow: WidgetFrameWindow = {
      postMessage,
    };

    expect(
      setWidgetOpenAiGlobals(frameWindow, { toolOutput: { ok: true } }),
    ).toBe("postMessage");
    expect(postMessage).toHaveBeenCalledWith(
      {
        type: "ax/openai:set_globals",
        globals: { toolOutput: { ok: true } },
      },
      "*",
    );
  });

  it("posts globals injection when direct frame access is blocked", () => {
    const postMessage = vi.fn();
    const frameWindow = {
      postMessage,
    } as WidgetFrameWindow;

    Object.defineProperty(frameWindow, "__axSetOpenAiGlobals", {
      get() {
        throw new DOMException(
          "Blocked a frame with origin from accessing a cross-origin frame.",
          "SecurityError",
        );
      },
    });

    expect(
      setWidgetOpenAiGlobals(frameWindow, { toolOutput: { ok: true } }),
    ).toBe("postMessage");
    expect(postMessage).toHaveBeenCalledWith(
      {
        type: "ax/openai:set_globals",
        globals: { toolOutput: { ok: true } },
      },
      "*",
    );
  });

  it("posts bridge messages to the frame", () => {
    const postMessage = vi.fn();
    const frameWindow: WidgetFrameWindow = {
      postMessage,
    };

    postWidgetFrameMessage(frameWindow, { jsonrpc: "2.0", method: "ui/test" });

    expect(postMessage).toHaveBeenCalledWith(
      { jsonrpc: "2.0", method: "ui/test" },
      "*",
    );
  });
});
