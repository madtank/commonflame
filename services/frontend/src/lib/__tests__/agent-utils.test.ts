import { describe, it, expect } from "vitest";
import {
  resolveExternalWebhookUrl,
  resolveExternalSubType,
  isExternalAgent,
} from "../agent-utils";

describe("resolveExternalWebhookUrl", () => {
  it("returns null for null/undefined agent", () => {
    expect(resolveExternalWebhookUrl(null)).toBeNull();
    expect(resolveExternalWebhookUrl(undefined)).toBeNull();
  });

  it("resolves webhook_url from top-level field", () => {
    const agent = { webhook_url: "https://example.com/webhook" };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://example.com/webhook",
    );
  });

  it("resolves webhookUrl (camelCase) from top-level field", () => {
    const agent = { webhookUrl: "https://example.com/webhook" };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://example.com/webhook",
    );
  });

  it("resolves external_webhook_url from top-level field", () => {
    const agent = { external_webhook_url: "https://example.com/external" };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://example.com/external",
    );
  });

  it("resolves externalWebhookUrl (camelCase) from top-level field", () => {
    const agent = { externalWebhookUrl: "https://example.com/external" };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://example.com/external",
    );
  });

  it("resolves from settings object", () => {
    const agent = {
      settings: { webhook_url: "https://settings.example.com/webhook" },
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://settings.example.com/webhook",
    );
  });

  it("resolves from settings.external_webhook_url", () => {
    const agent = {
      settings: {
        external_webhook_url: "https://settings.example.com/external",
      },
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://settings.example.com/external",
    );
  });

  it("resolves from stringified JSON settings", () => {
    const agent = {
      settings: JSON.stringify({
        webhook_url: "https://json-settings.example.com/webhook",
      }),
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://json-settings.example.com/webhook",
    );
  });

  it("resolves from stringified JSON settings with external_webhook_url", () => {
    const agent = {
      settings: JSON.stringify({
        external_webhook_url: "https://json-settings.example.com/external",
      }),
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://json-settings.example.com/external",
    );
  });

  it("resolves from metadata object", () => {
    const agent = {
      metadata: { webhook_url: "https://metadata.example.com/webhook" },
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://metadata.example.com/webhook",
    );
  });

  it("resolves from stringified JSON metadata", () => {
    const agent = {
      metadata: JSON.stringify({
        webhook_url: "https://json-metadata.example.com/webhook",
      }),
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://json-metadata.example.com/webhook",
    );
  });

  it("prioritizes top-level fields over nested fields", () => {
    const agent = {
      webhook_url: "https://top-level.example.com",
      settings: { webhook_url: "https://settings.example.com" },
      metadata: { webhook_url: "https://metadata.example.com" },
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://top-level.example.com",
    );
  });

  it("falls back to settings when top-level is empty", () => {
    const agent = {
      webhook_url: "",
      settings: { webhook_url: "https://settings.example.com" },
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://settings.example.com",
    );
  });

  it("trims whitespace from URLs", () => {
    const agent = { webhook_url: "  https://example.com/webhook  " };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://example.com/webhook",
    );
  });

  it("returns null for empty string values", () => {
    const agent = { webhook_url: "", settings: { webhook_url: "" } };
    expect(resolveExternalWebhookUrl(agent)).toBeNull();
  });

  it("returns null for whitespace-only values", () => {
    const agent = { webhook_url: "   " };
    expect(resolveExternalWebhookUrl(agent)).toBeNull();
  });

  it("handles invalid JSON in settings gracefully", () => {
    const agent = {
      settings: "not valid json {",
      metadata: { webhook_url: "https://fallback.example.com" },
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://fallback.example.com",
    );
  });

  it("handles real-world Clawdbot agent structure", () => {
    // Simulate what the backend might return for a Clawdbot agent
    const agent = {
      id: "agent-123",
      username: "ax_sentinel",
      origin: "external_gateway",
      agent_type: "general",
      settings: JSON.stringify({
        sub_type: "clawdbot",
        external_webhook_url: "https://xyz.trycloudflare.com/ax/dispatch",
      }),
    };
    expect(resolveExternalWebhookUrl(agent)).toBe(
      "https://xyz.trycloudflare.com/ax/dispatch",
    );
  });
});

describe("resolveExternalSubType", () => {
  it("returns null for null/undefined agent", () => {
    expect(resolveExternalSubType(null)).toBeNull();
    expect(resolveExternalSubType(undefined)).toBeNull();
  });

  it("resolves sub_type from capabilities", () => {
    const agent = { capabilities: { sub_type: "clawdbot" } };
    expect(resolveExternalSubType(agent)).toBe("clawdbot");
  });

  it("resolves from top-level sub_type", () => {
    const agent = { sub_type: "moltbot" };
    expect(resolveExternalSubType(agent)).toBe("moltbot");
  });

  it("resolves from settings object", () => {
    const agent = { settings: { sub_type: "clawdbot" } };
    expect(resolveExternalSubType(agent)).toBe("clawdbot");
  });

  it("resolves from stringified JSON settings", () => {
    const agent = { settings: JSON.stringify({ sub_type: "clawdbot" }) };
    expect(resolveExternalSubType(agent)).toBe("clawdbot");
  });
});

describe("isExternalAgent", () => {
  it("returns false for null/undefined agent", () => {
    expect(isExternalAgent(null)).toBe(false);
    expect(isExternalAgent(undefined)).toBe(false);
  });

  it("returns true for agent with external_gateway origin", () => {
    const agent = { origin: "external_gateway" };
    expect(isExternalAgent(agent)).toBe(true);
  });

  it("returns true for agent with external_gateway agent_type", () => {
    const agent = { agent_type: "external_gateway" };
    expect(isExternalAgent(agent)).toBe(true);
  });

  it("returns true for agent with external sub_type", () => {
    const agent = { sub_type: "clawdbot" };
    expect(isExternalAgent(agent)).toBe(true);
  });

  it("returns true for agent with webhook_url", () => {
    const agent = { webhook_url: "https://example.com" };
    expect(isExternalAgent(agent)).toBe(true);
  });

  it("returns false for regular cloud agent", () => {
    const agent = {
      origin: "cloud",
      agent_type: "general",
      enable_cloud_agent: true,
    };
    expect(isExternalAgent(agent)).toBe(false);
  });
});
