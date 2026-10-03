import { render, screen, userEvent } from "@/test/utils";
import { describe, expect, it, vi } from "vitest";
import {
  AX_SHELL_HEADER_ACTIONS_CLASS,
  AX_SHELL_HEADER_BRAND_CLASS,
  AX_SHELL_HEADER_ROW_CLASS,
  AX_SHELL_HEADER_SAFE_AREA_CLASS,
  AX_SHELL_MOBILE_VIEWPORT_CLASS,
  AX_SHELL_QUICK_MENU_WRAPPER_CLASS,
  AX_SHELL_SPACE_SWITCHER_CLASS,
  AX_SHELL_SPACE_SWITCHER_LABEL_CLASS,
  AxMcpAppScreenChrome,
  buildContextExplorerPanelWidgetFromDirectHtml,
  buildDirectHtmlContextPanelWidget,
  CONTEXT_ALL_HOST_COMMAND,
  getCompactSpaceSelectWidthCh,
  isDirectHtmlContextPanelWidget,
  launcherItems,
  shouldShowExpandedMentionShortcuts,
  SPACE_SWITCHER_NATIVE_SELECT_HIT_TARGET_CLASS,
  stripLeadingDirectedMention,
  TASKS_ALL_HOST_COMMAND,
} from "./AxPlatformShell";

describe("task app shell host command contract", () => {
  it("uses the widget-supported all-tasks host command for the shell top-right All tasks control", () => {
    expect(TASKS_ALL_HOST_COMMAND).toBe("tasks/all");
  });

  it("uses the widget-supported all-context host command for the shell top-right All context control", () => {
    expect(CONTEXT_ALL_HOST_COMMAND).toBe("context/all");
  });
});

describe("MCP app launcher", () => {
  it("does not include the dead Groups tile", () => {
    expect(launcherItems.map((item) => item.id)).not.toContain("agent_groups");
    expect(launcherItems.map((item) => item.title)).not.toContain("Groups");
  });
});

describe("composer mention shortcuts", () => {
  it("hides the expanded @ mentions shortcuts when the selector rail is pinned", () => {
    expect(shouldShowExpandedMentionShortcuts(true, 4)).toBe(false);
  });

  it("shows expanded @ mentions shortcuts only when unpinned agents are available", () => {
    expect(shouldShowExpandedMentionShortcuts(false, 4)).toBe(true);
    expect(shouldShowExpandedMentionShortcuts(false, 0)).toBe(false);
  });
});

describe("mobile app shell viewport guards", () => {
  it("pins the modern shell to the mobile visual viewport so body scroll cannot hide the header", () => {
    expect(AX_SHELL_MOBILE_VIEWPORT_CLASS.split(/\s+/)).toEqual(
      expect.arrayContaining([
        "fixed",
        "inset-0",
        "overflow-hidden",
        "sm:relative",
        "sm:h-[100dvh]",
      ]),
    );
  });

  it("reserves top safe-area space for the mobile header", () => {
    expect(AX_SHELL_HEADER_SAFE_AREA_CLASS).toContain(
      "env(safe-area-inset-top",
    );
  });
});

describe("mobile shell header overflow guards", () => {
  it("lets the workspace selector shrink before the settings gear can be pushed off-screen", () => {
    const headerRowClasses = AX_SHELL_HEADER_ROW_CLASS.split(/\s+/);
    expect(headerRowClasses).toEqual(
      expect.arrayContaining(["min-w-0", "gap-2"]),
    );
    expect(headerRowClasses).not.toContain("overflow-hidden");
    expect(AX_SHELL_HEADER_BRAND_CLASS.split(/\s+/)).toContain("shrink-0");
    expect(AX_SHELL_HEADER_ACTIONS_CLASS.split(/\s+/)).toEqual(
      expect.arrayContaining(["min-w-0", "flex-1", "justify-end"]),
    );
    expect(AX_SHELL_QUICK_MENU_WRAPPER_CLASS.split(/\s+/)).toContain(
      "shrink-0",
    );
  });

  it("keeps fixed desktop selector widths behind the sm breakpoint on mobile", () => {
    const switcherClasses = AX_SHELL_SPACE_SWITCHER_CLASS.split(/\s+/);
    expect(switcherClasses).toEqual(
      expect.arrayContaining(["min-w-0", "flex-1", "sm:w-fit"]),
    );
    expect(switcherClasses).not.toContain("w-fit");
    expect(switcherClasses).not.toContain(
      "max-w-[min(21rem,calc(100vw-10rem))]",
    );

    const labelClasses = AX_SHELL_SPACE_SWITCHER_LABEL_CLASS.split(/\s+/);
    expect(labelClasses).toEqual(
      expect.arrayContaining(["min-w-0", "flex-1", "truncate"]),
    );
    expect(labelClasses).not.toContain("max-w-[min(10rem,calc(100vw-12rem))]");
  });
});

describe("space switcher sizing helpers", () => {
  it("sizes the selected-space select snugly from the current label instead of the longest option", () => {
    const spaces = [
      {
        id: "current",
        name: "madtank's Workspace",
        visibility: "private",
      },
      {
        id: "long",
        name: "UAT Long Space Name For Dropdown Truncation Review",
        visibility: "invite_only",
      },
    ];

    expect(getCompactSpaceSelectWidthCh(spaces[0], spaces)).toBe(9);
    expect(getCompactSpaceSelectWidthCh(spaces[1], spaces)).toBe(15);
  });

  it("does not add trailing character space to short selected space names", () => {
    const spaces = [
      {
        id: "short",
        name: "Labs",
        visibility: "private",
      },
    ];

    expect(getCompactSpaceSelectWidthCh(spaces[0], spaces)).toBe(4);
  });

  it("keeps the native select stretched over the full switcher pill", () => {
    expect(SPACE_SWITCHER_NATIVE_SELECT_HIT_TARGET_CLASS.split(/\s+/)).toEqual(
      expect.arrayContaining([
        "absolute",
        "inset-0",
        "h-full",
        "w-full",
        "cursor-pointer",
        "opacity-0",
      ]),
    );
  });
});

describe("direct HTML context app panels", () => {
  it("unwraps selected HTML context artifacts so games open without the explorer chrome", () => {
    const html = `<!doctype html><html><body><canvas id="game"></canvas></body></html>`;
    const widget = buildDirectHtmlContextPanelWidget({
      kind: "mcp_app",
      tool_name: "context",
      tool_action: "get",
      resource_uri: "ui://context/explorer",
      title: "Context Explorer",
      initial_data: {
        selected_key: "tap-defense-mobile-missile-command-baseline-2026-05-24",
        items: [
          {
            key: "tap-defense-mobile-missile-command-baseline-2026-05-24",
            value: {
              type: "html",
              title: "Tap Defense — Mobile Missile Command (approved baseline)",
              filename: "tap-defense.html",
              content_type: "text/html",
              html,
            },
          },
        ],
      },
    });

    expect(widget).toEqual({
      title: "Tap Defense — Mobile Missile Command (approved baseline)",
      widget: expect.objectContaining({
        html,
        resource_uri: undefined,
        resource_url: undefined,
        title: "Tap Defense — Mobile Missile Command (approved baseline)",
        tool_action: "render_html",
        lifecycle: "complete",
      }),
    });
  });

  it("keeps ordinary context widgets in the explorer", () => {
    const widget = buildDirectHtmlContextPanelWidget({
      kind: "mcp_app",
      tool_name: "context",
      resource_uri: "ui://context/explorer",
      initial_data: {
        selected_key: "notes",
        items: [{ key: "notes", value: "plain text" }],
      },
    });

    expect(widget).toBeNull();
  });

  it("restores direct HTML context panels back to the Context Explorer descriptor", () => {
    const directWidget = {
      kind: "mcp_app",
      tool_name: "context",
      tool_action: "render_html",
      title: "Mini Arcade",
      html: "<!doctype html><html><body>game</body></html>",
      display_mode: "fullscreen",
      lifecycle: "complete",
      initial_data: {
        selected_key: "games/mini-arcade-html",
        items: [],
      },
    } as const;

    expect(isDirectHtmlContextPanelWidget(directWidget)).toBe(true);

    const restored =
      buildContextExplorerPanelWidgetFromDirectHtml(directWidget);

    expect(restored).toEqual(
      expect.objectContaining({
        resource_uri: "ui://context-explorer",
        title: "Context",
        tool_action: "list",
        display_mode: "fullscreen",
      }),
    );
    expect(restored).not.toHaveProperty("html");
    expect(restored).not.toHaveProperty("resource_url");
  });
});

describe("AxMcpAppScreenChrome", () => {
  it("presents opened Tasks widgets as the active MCP app surface", async () => {
    const onBackToActivity = vi.fn();
    const onShowAllTasks = vi.fn();

    render(
      <AxMcpAppScreenChrome
        title="Tasks"
        isTasks
        isContext={false}
        isSearch={false}
        isDarkMode
        bottomInset={120}
        onBackToActivity={onBackToActivity}
        onShowAllContext={vi.fn()}
        onShowAllTasks={onShowAllTasks}
      >
        <section data-testid="task-list-proof">Task list state</section>
      </AxMcpAppScreenChrome>,
    );

    const screenRoot = screen.getByTestId("ax-mcp-app-panel");
    expect(screenRoot).toHaveAttribute("data-active-screen", "mcp-app");
    expect(screenRoot).toHaveClass(
      "fixed",
      "inset-x-0",
      "top-0",
      "z-[65]",
      "sm:z-[80]",
      "bottom-[var(--mcp-app-panel-mobile-bottom)]",
      "sm:bottom-0",
      "overflow-hidden",
    );
    expect(screenRoot).toHaveStyle({
      "--mcp-app-panel-mobile-bottom": "104px",
    });
    expect(screen.getByTestId("ax-mcp-app-screen-header")).toHaveClass(
      "h-16",
      "shrink-0",
    );
    expect(screen.getByTestId("ax-mcp-app-screen-body")).toHaveClass(
      "min-h-0",
      "flex-1",
      "overflow-hidden",
    );
    expect(screen.getByTestId("task-list-proof")).toHaveTextContent(
      "Task list state",
    );
    expect(screen.getByTestId("ax-mcp-app-screen-mode")).toHaveTextContent(
      "MCP App",
    );
    expect(screen.getByTestId("ax-mcp-app-screen-mode")).not.toHaveTextContent(
      "just now",
    );

    await userEvent.click(screen.getByTestId("ax-mcp-app-all-tasks"));
    expect(onShowAllTasks).toHaveBeenCalledTimes(1);

    await userEvent.click(
      screen.getByRole("button", { name: "Back to activity stream" }),
    );
    expect(onBackToActivity).toHaveBeenCalledTimes(1);
  });

  it("uses compact chrome for immersive HTML app panels", () => {
    render(
      <AxMcpAppScreenChrome
        title="Tap Defense — Mobile Missile Command"
        isTasks={false}
        isContext={false}
        isSearch={false}
        isDarkMode
        immersive
        onBackToActivity={vi.fn()}
        onShowAllContext={vi.fn()}
        onShowAllTasks={vi.fn()}
      >
        <section data-testid="game-proof">Game canvas</section>
      </AxMcpAppScreenChrome>,
    );

    expect(screen.getByTestId("ax-mcp-app-screen-header")).toHaveClass("h-12");
    expect(screen.getByTestId("ax-mcp-app-screen-title")).toHaveTextContent(
      "Tap Defense",
    );
    expect(
      screen.queryByTestId("ax-mcp-app-screen-mode"),
    ).not.toBeInTheDocument();
  });

  it("uses an All context control instead of the Activity back button for Context widgets", async () => {
    const onBackToActivity = vi.fn();
    const onShowAllContext = vi.fn();

    render(
      <AxMcpAppScreenChrome
        title="Context"
        isTasks={false}
        isContext
        isSearch={false}
        isDarkMode
        onBackToActivity={onBackToActivity}
        onShowAllContext={onShowAllContext}
        onShowAllTasks={vi.fn()}
      >
        <section data-testid="context-proof">Context browser</section>
      </AxMcpAppScreenChrome>,
    );

    expect(screen.queryByTestId("ax-mcp-app-back")).not.toBeInTheDocument();
    expect(screen.getByTestId("context-proof")).toHaveTextContent(
      "Context browser",
    );

    await userEvent.click(screen.getByTestId("ax-mcp-app-all-context"));
    expect(onShowAllContext).toHaveBeenCalledTimes(1);
    expect(onBackToActivity).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("ax-mcp-app-close"));
    expect(onBackToActivity).toHaveBeenCalledTimes(1);
  });

  it("renders task detail content in the same active app body with close/back affordance", async () => {
    const onBackToActivity = vi.fn();

    render(
      <AxMcpAppScreenChrome
        title="Activity stream visual target"
        isTasks
        isContext={false}
        isSearch={false}
        isDarkMode={false}
        onBackToActivity={onBackToActivity}
        onShowAllContext={vi.fn()}
        onShowAllTasks={vi.fn()}
      >
        <article data-testid="task-detail-proof">
          Task detail bottom controls
        </article>
      </AxMcpAppScreenChrome>,
    );

    expect(screen.getByTestId("ax-mcp-app-panel")).toHaveClass(
      "fixed",
      "inset-x-0",
      "top-0",
    );
    expect(
      screen.getByText("Activity stream visual target"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("task-detail-proof")).toHaveTextContent(
      "Task detail bottom controls",
    );

    await userEvent.click(
      screen.getByRole("button", {
        name: "Close app panel",
      }),
    );
    expect(onBackToActivity).toHaveBeenCalledTimes(1);
  });
});

describe("stripLeadingDirectedMention", () => {
  it("strips the single leading mention matching the route label", () => {
    expect(
      stripLeadingDirectedMention("@code_weaver follow up", "@code_weaver"),
    ).toBe("follow up");
  });

  it("strips every leading mention when the label lists multiple recipients", () => {
    expect(
      stripLeadingDirectedMention(
        "@code_weaver @data_diva standup time",
        "@code_weaver · @data_diva",
      ),
    ).toBe("standup time");
  });

  it("strips nothing when the label does not cover every leading mention", () => {
    // Partial stripping misleads: with @code_weaver and @other both leading
    // but only @code_weaver in the label, removing one makes the message
    // read as if it went only to @other. Show exactly what was sent instead.
    expect(
      stripLeadingDirectedMention("@code_weaver @other hello", "@code_weaver"),
    ).toBe("@code_weaver @other hello");
  });

  it("strips nothing when a server-echoed label names only the primary of several recipients", () => {
    expect(
      stripLeadingDirectedMention(
        "@canvas @canary we're making progress",
        "Canvas",
      ),
    ).toBe("@canvas @canary we're making progress");
  });

  it("returns the original content when stripping would leave it empty", () => {
    expect(
      stripLeadingDirectedMention(
        "@code_weaver @data_diva",
        "@code_weaver · @data_diva",
      ),
    ).toBe("@code_weaver @data_diva");
  });
});
