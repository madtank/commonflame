import { beforeEach, describe, expect, it, vi } from "vitest";

const restoreLocalStorage = (descriptor: PropertyDescriptor | undefined | null) => {
  if (!descriptor) return;
  Object.defineProperty(window, "localStorage", descriptor);
};

describe("safeLocalStorage", () => {
  beforeEach(() => {
    vi.resetModules();
    // Clear localStorage between tests
    window.localStorage.clear();
  });

  it("uses native storage when available", async () => {
    const { safeLocalStorage } = await import("./safe-local-storage");

    safeLocalStorage.__resetForTests();
    safeLocalStorage.setItem("native:test", "value");
    expect(safeLocalStorage.getItem("native:test")).toBe("value");
    safeLocalStorage.removeItem("native:test");
    expect(safeLocalStorage.isUsingMemory()).toBe(false);
  });

  it("falls back to in-memory storage when native storage throws", async () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");

    const failingStorage: Storage = {
      getItem: vi.fn(() => {
        throw new Error("blocked");
      }),
      setItem: vi.fn(() => {
        throw new Error("blocked");
      }),
      removeItem: vi.fn(() => {
        throw new Error("blocked");
      }),
      clear: vi.fn(() => {
        throw new Error("blocked");
      }),
      key: vi.fn(() => null),
      length: 0,
    } as unknown as Storage;

    Object.defineProperty(window, "localStorage", {
      value: failingStorage,
      configurable: true,
    });

    const { safeLocalStorage } = await import("./safe-local-storage");
    safeLocalStorage.__resetForTests();

    // Should not throw and should retain value via memory fallback
    safeLocalStorage.setItem("memory:test", "value");
    expect(safeLocalStorage.getItem("memory:test")).toBe("value");
    expect(safeLocalStorage.isUsingMemory()).toBe(true);

    restoreLocalStorage(originalDescriptor);
  });
});
