import "@testing-library/jest-dom";
import { webcrypto } from "node:crypto";
import { afterEach, beforeAll, afterAll, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { server } from "./mocks/server";

// Mock Service Worker setup
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  // Clean up DOM after each test
  cleanup();
  // Reset MSW handlers
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

// Node 18 + jsdom does not reliably expose Web Crypto SubtleCrypto.
// Cognito PKCE helpers use crypto.getRandomValues and crypto.subtle.digest.
if (!globalThis.crypto || !globalThis.crypto.subtle) {
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: webcrypto,
  });
}

// Mock window.matchMedia
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(), // Deprecated
    removeListener: vi.fn(), // Deprecated
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock window.ResizeObserver
class MockResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();

  constructor(_callback: ResizeObserverCallback) {}
}

global.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;

// Mock scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

// Mock localStorage with a real Map-based implementation
const createLocalStorageMock = () => {
  const store = new Map<string, string>();
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    get length() {
      return store.size;
    },
    key: (index: number) => {
      const keys = Array.from(store.keys());
      return keys[index] ?? null;
    },
  };
};
global.localStorage = createLocalStorageMock() as any;

// Mock console.warn for cleaner test output
global.console = {
  ...console,
  warn: vi.fn(),
};

// Provide a minimal IntersectionObserver mock (used by MessageList)
if (!(global as any).IntersectionObserver) {
  class MockIntersectionObserver {
    readonly root: Element | null = null;
    readonly rootMargin: string = "0px";
    readonly thresholds: ReadonlyArray<number> = [0];
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
    takeRecords = vi.fn(() => []);
  }
  (global as any).IntersectionObserver = MockIntersectionObserver as any;
}

// Jest compatibility shim for legacy tests referencing jest.* APIs
// Allows using jest.mock / jest.fn while running under Vitest
if (!(global as any).jest) {
  (global as any).jest = {
    fn: vi.fn.bind(vi),
    mock: vi.mock.bind(vi),
    spyOn: vi.spyOn.bind(vi),
    clearAllMocks: vi.clearAllMocks.bind(vi),
    resetAllMocks: vi.resetAllMocks.bind(vi),
    restoreAllMocks: vi.restoreAllMocks.bind(vi),
  } as any;
}
