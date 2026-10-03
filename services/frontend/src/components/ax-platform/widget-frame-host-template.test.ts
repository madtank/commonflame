import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const frameHostHtml = readFileSync(
  resolve(process.cwd(), "public/ax-mcp-frame-host.html"),
  "utf8",
);

describe("ax-mcp-frame-host template", () => {
  it("exposes direct theme updates and accepts standard MCP host-context changes", () => {
    expect(frameHostHtml).toContain("window.__axSetTheme = applyTheme;");
    expect(frameHostHtml).toContain('data.method === "ui/notifications/host-context-changed"');
    expect(frameHostHtml).toContain("applyTheme(data.params.theme);");
    expect(frameHostHtml).not.toContain('data.type === "ax/theme"');
  });

  it("injects a portable light-theme token adapter for MCP widgets", () => {
    expect(frameHostHtml).toContain('id="ax-widget-theme-tokens"');
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"],body[data-theme="light"]',
    );
    expect(frameHostHtml).toContain("--ax-bg:#f8fafc;");
    expect(frameHostHtml).toContain("--ax-surface:#ffffff;");
    expect(frameHostHtml).toContain("--ax-text:#0f172a;");
    expect(frameHostHtml).toContain("--ax-surface-header:#f1f5f9;");
    expect(frameHostHtml).toContain("--ax-row-hover-bg:rgba(8,145,178,0.10);");
    expect(frameHostHtml).toContain("--ax-selected-bg:#e0f2fe;");
    expect(frameHostHtml).toContain("--ax-artifact-surface:#ffffff;");
    expect(frameHostHtml).toContain("--ax-code-bg:#e2e8f0;");
    expect(frameHostHtml).toContain("--prose-body:#0f172a;");
    expect(frameHostHtml).toContain("--bg:#f8fafc;");
    expect(frameHostHtml).toContain("--surface:#ffffff;");
    expect(frameHostHtml).toContain("--text:#0f172a;");
  });

  it("adds light-mode fallback styles for markdown artifact renderers", () => {
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] body.artifact-mode',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] .artifact-document',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] .upload-markdown',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] .upload-markdown pre.md-code',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] .upload-markdown code.md-inline',
    );
  });

  it("adds light-mode compatibility styles for Spaces and Identity widgets", () => {
    expect(frameHostHtml).toContain(':root[data-theme="light"] .panel-header');
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] .space-row,:root[data-theme="light"] .memory-item',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] .space-row:hover,:root[data-theme="light"] .memory-item:hover',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="light"] .tab.active,:root[data-theme="light"] .chip.active',
    );
  });

  it("adds dark-mode task widget contrast compatibility styles", () => {
    expect(frameHostHtml).toContain(
      ':root[data-theme="dark"],body[data-theme="dark"],html.dark,body.dark',
    );
    expect(frameHostHtml).toContain("--ax-text-muted:#94a3b8;");
    expect(frameHostHtml).toContain(
      ':is(:root[data-theme="dark"],body[data-theme="dark"],html.dark,body.dark) .filter-row-label,:is(:root[data-theme="dark"],body[data-theme="dark"],html.dark,body.dark) .toolbar-label',
    );
    expect(frameHostHtml).toContain(
      ':is(:root[data-theme="dark"],body[data-theme="dark"],html.dark,body.dark) .filter-btn{background:rgba(15,23,42,0.92)!important',
    );
    expect(frameHostHtml).toContain(
      ':is(:root[data-theme="dark"],body[data-theme="dark"],html.dark,body.dark) .filter-btn[data-testid="tasks-filter-open"].active',
    );
    expect(frameHostHtml).toContain(
      ':is(:root[data-theme="dark"],body[data-theme="dark"],html.dark,body.dark) .filter-btn[data-testid="tasks-priority-medium"].active',
    );
    expect(frameHostHtml).toContain(
      "background-image:none!important;border-color:rgba(148,163,184,0.22)!important;color:#cbd5e1!important;box-shadow:none!important;text-shadow:none!important;filter:none!important",
    );
    expect(frameHostHtml).toContain(
      ':is(:root[data-theme="dark"],body[data-theme="dark"],html.dark,body.dark) .filter-btn *{color:inherit!important;text-shadow:none!important;}',
    );
    expect(frameHostHtml).toContain(
      '@media (prefers-color-scheme:dark){body:not([data-theme="light"]) .filter-btn',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="dark"] .status-open{background:rgba(59,130,246,0.14)!important;color:#bfdbfe!important;',
    );
    expect(frameHostHtml).toContain(
      ':root[data-theme="dark"] .prio-btn[data-prio="medium"].active{background:rgba(245,158,11,0.22)!important;',
    );
  });

  it("exposes task sort labels as accessible buttons in injected widget payloads", () => {
    expect(frameHostHtml).toContain(
      "function buildInjectedTaskSortA11yScript()",
    );
    expect(frameHostHtml).toContain("tasks-sort-");
    expect(frameHostHtml).toContain("Sort tasks by");
    expect(frameHostHtml).toContain("button[data-ax-task-sort]");
  });

  it("adds theme tokens before the widget bridge script", () => {
    const bodyInjectionIndex = frameHostHtml.indexOf(
      "+ themeStyle + bridgeScript",
    );
    const fallbackInjectionIndex = frameHostHtml.indexOf(
      "themeStyle + bridgeScript + taskSortA11yScript + html",
    );

    expect(bodyInjectionIndex).toBeGreaterThan(-1);
    expect(fallbackInjectionIndex).toBeGreaterThan(-1);
  });
});
