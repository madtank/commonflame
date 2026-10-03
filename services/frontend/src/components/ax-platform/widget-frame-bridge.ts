export type WidgetFrameWindow = Pick<Window, "postMessage"> & {
  __axSetOpenAiGlobals?: (globals: Record<string, unknown>) => void;
  __axSetTheme?: (theme: "light" | "dark") => void;
};

export function postWidgetFrameMessage(
  frameWindow: WidgetFrameWindow,
  payload: unknown,
) {
  frameWindow.postMessage(payload, "*");
}

export function setWidgetFrameTheme(
  frameWindow: WidgetFrameWindow,
  theme: "light" | "dark",
) {
  try {
    if (typeof frameWindow.__axSetTheme === "function") {
      frameWindow.__axSetTheme(theme);
      return "direct";
    }
  } catch {
    // Cross-origin frames use the MCP Apps transport instead of a named hook.
  }

  postWidgetFrameMessage(frameWindow, {
    jsonrpc: "2.0",
    method: "ui/notifications/host-context-changed",
    params: { theme },
  });
  return "postMessage";
}

export function setWidgetOpenAiGlobals(
  frameWindow: WidgetFrameWindow,
  globals: Record<string, unknown>,
) {
  try {
    if (typeof frameWindow.__axSetOpenAiGlobals === "function") {
      frameWindow.__axSetOpenAiGlobals(globals);
      return "direct";
    }
  } catch {
    // Cross-origin frames cannot expose named window properties to the host.
  }

  frameWindow.postMessage(
    {
      type: "ax/openai:set_globals",
      globals,
    },
    "*",
  );
  return "postMessage";
}
