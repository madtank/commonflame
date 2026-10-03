import { describe, expect, it } from "vitest";
import {
  getSseLatencyMs,
  isRemoteLoopbackApiTarget,
  resolveApiBaseUrl,
  resolveDirectApiUrl,
  resolveDirectMcpUrl,
} from "./runtime-origin";

describe("runtime-origin", () => {
  it("uses the explicit API URL when one is configured", () => {
    expect(
      resolveDirectApiUrl({
        envUrl: "https://waystation.example/api/",
        isDev: false,
      }),
    ).toBe("https://waystation.example/api");
  });

  it("preserves an explicit API origin with no path", () => {
    expect(
      resolveDirectApiUrl({
        envUrl: "https://waystation.example/",
        isDev: false,
      }),
    ).toBe("https://waystation.example");
  });

  it("preserves an explicit staging origin for HTTP API calls", () => {
    expect(
      resolveApiBaseUrl({
        envUrl: "https://dev.waystation.example/",
        isDev: false,
      }),
    ).toBe("https://dev.waystation.example");
  });

  it("keeps staging builds on the host root when the env omits /api", () => {
    expect(
      resolveApiBaseUrl({
        envUrl: "https://dev.waystation.example/",
        isDev: false,
      }),
    ).toBe("https://dev.waystation.example");
    expect(
      resolveDirectApiUrl({
        envUrl: "https://dev.waystation.example/",
        isDev: false,
      }),
    ).toBe("https://dev.waystation.example");
  });

  it("preserves a configured /api mount for staging direct API calls", () => {
    expect(
      resolveDirectApiUrl({
        envUrl: "https://dev.waystation.example/",
        isDev: false,
      }),
    ).toBe("https://dev.waystation.example");
  });

  it("preserves custom path prefixes other than /api", () => {
    expect(
      resolveApiBaseUrl({
        envUrl: "https://dev.waystation.example/backend/",
        isDev: false,
      }),
    ).toBe("https://dev.waystation.example/backend");
  });

  it("uses relative API paths during local Vite development", () => {
    expect(
      resolveApiBaseUrl({
        envUrl: "http://localhost:8001",
        isDev: true,
      }),
    ).toBe("");
  });

  it("falls back to same-origin for deployed builds", () => {
    expect(
      resolveDirectApiUrl({
        isDev: false,
        windowLocation: {
          origin: "https://waystation.example",
          protocol: "https:",
          hostname: "waystation.example",
        },
      }),
    ).toBe("https://waystation.example");
  });

  it("keeps an arbitrary hosted deployment on its own origin", () => {
    const cloudFrontLocation = {
      origin: "https://d111111abcdef8.cloudfront.net",
      protocol: "https:",
      hostname: "d111111abcdef8.cloudfront.net",
    };

    expect(
      resolveApiBaseUrl({
        isDev: false,
        windowLocation: cloudFrontLocation,
      }),
    ).toBe(cloudFrontLocation.origin);
    expect(
      resolveDirectApiUrl({
        isDev: false,
        windowLocation: cloudFrontLocation,
      }),
    ).toBe(cloudFrontLocation.origin);
  });

  it("uses same-origin API proxy during local development", () => {
    expect(
      resolveDirectApiUrl({
        isDev: true,
        windowLocation: {
          origin: "http://localhost:3000",
          protocol: "http:",
          hostname: "localhost",
        },
      }),
    ).toBe("http://localhost:3000");
  });

  it("uses same-origin MCP proxy during local development", () => {
    expect(
      resolveDirectMcpUrl({
        isDev: true,
        windowLocation: {
          origin: "http://localhost:3000",
          protocol: "http:",
          hostname: "localhost",
        },
      }),
    ).toBe("http://localhost:3000");
  });

  it("flags loopback backends on remote pages", () => {
    expect(
      isRemoteLoopbackApiTarget("http://127.0.0.1:8001", "https://waystation.example"),
    ).toBe(true);
    expect(
      isRemoteLoopbackApiTarget(
        "http://localhost:8001",
        "http://localhost:3000",
      ),
    ).toBe(false);
  });

  it("clamps small future skew and drops obviously broken timestamps", () => {
    expect(
      getSseLatencyMs(
        "2026-03-26T10:00:10.000Z",
        Date.parse("2026-03-26T10:00:00.000Z"),
      ),
    ).toBe(0);
    expect(
      getSseLatencyMs(
        "2099-01-01T00:00:00.000Z",
        Date.parse("2026-03-26T10:00:00.000Z"),
      ),
    ).toBeNull();
    expect(
      getSseLatencyMs(
        "2026-03-26T09:59:58.500Z",
        Date.parse("2026-03-26T10:00:00.000Z"),
      ),
    ).toBe(1500);
  });
});
