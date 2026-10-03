/**
 * Tests for display utility functions.
 *
 * Spec: WIDGET-002 §5.1 — Universal Display Rules
 * - "Hide or transform: backend enum names"
 * - "Hide or transform: UUIDs"
 * - "Hide: technical transport/runtime metadata"
 */
import { describe, it, expect } from "vitest";
import {
  humanizeAgentType,
  humanizeHandle,
  formatSpaceLabel,
} from "./display-utils";

describe("humanizeAgentType", () => {
  it("converts SCREAMING_SNAKE backend enums to human labels", () => {
    expect(humanizeAgentType("EXTERNAL_GATEWAY")).toBe("External Gateway");
    expect(humanizeAgentType("SPACE_AGENT")).toBe("Space Agent");
    expect(humanizeAgentType("CLOUD_GCP")).toBe("Cloud");
  });

  it("converts lowercase snake_case enums", () => {
    expect(humanizeAgentType("external_gateway")).toBe("External Gateway");
    expect(humanizeAgentType("space_agent")).toBe("Space Agent");
  });

  it("passes through already-human labels unchanged", () => {
    expect(humanizeAgentType("CLI")).toBe("CLI");
    expect(humanizeAgentType("MCP")).toBe("MCP");
  });

  it("handles known shorthand types", () => {
    expect(humanizeAgentType("cli")).toBe("CLI");
    expect(humanizeAgentType("mcp")).toBe("MCP");
  });

  it("returns fallback for null/undefined/empty", () => {
    expect(humanizeAgentType(null)).toBe("Agent");
    expect(humanizeAgentType(undefined)).toBe("Agent");
    expect(humanizeAgentType("")).toBe("Agent");
  });

  it("handles general type", () => {
    expect(humanizeAgentType("general")).toBe("General");
    expect(humanizeAgentType("GENERAL")).toBe("General");
  });

  it("maps special types from legacy agentTypeLabel", () => {
    expect(humanizeAgentType("webhook")).toBe("OpenClaw");
    expect(humanizeAgentType("user")).toBe("Human");
  });
});

describe("humanizeHandle", () => {
  it("converts snake_case handles to title case", () => {
    expect(humanizeHandle("wire_tap")).toBe("Wire Tap");
    expect(humanizeHandle("logic_runner_677")).toBe("Logic Runner 677");
    expect(humanizeHandle("nova_sage")).toBe("Nova Sage");
  });

  it("preserves abbreviations", () => {
    expect(humanizeHandle("mcp_gateway")).toBe("MCP Gateway");
    expect(humanizeHandle("cli_tool")).toBe("CLI Tool");
  });

  it("handles aX special case", () => {
    expect(humanizeHandle("ax")).toBe("Waystation");
    expect(humanizeHandle("@ax")).toBe("Waystation");
  });

  it("strips @ prefix", () => {
    expect(humanizeHandle("@wire_tap")).toBe("Wire Tap");
  });

  it("returns fallback for null/undefined/empty", () => {
    expect(humanizeHandle(null)).toBe("Agent");
    expect(humanizeHandle(undefined)).toBe("Agent");
    expect(humanizeHandle("")).toBe("Agent");
  });
});

describe("formatSpaceLabel", () => {
  it("shows workspace name without UUID when name is available", () => {
    expect(
      formatSpaceLabel("My Workspace", "12d6eafd-0316-4f3e-be33-fd8a3fd90f67"),
    ).toBe("My Workspace");
  });

  it("falls back to truncated UUID when name is missing", () => {
    expect(formatSpaceLabel(null, "12d6eafd-0316-4f3e-be33-fd8a3fd90f67")).toBe(
      "12d6eafd…",
    );
  });

  it("falls back to truncated UUID for empty name", () => {
    expect(formatSpaceLabel("", "12d6eafd-0316-4f3e-be33-fd8a3fd90f67")).toBe(
      "12d6eafd…",
    );
  });

  it("returns empty string when both are missing", () => {
    expect(formatSpaceLabel(null, null)).toBe("");
    expect(formatSpaceLabel(undefined, undefined)).toBe("");
  });
});
