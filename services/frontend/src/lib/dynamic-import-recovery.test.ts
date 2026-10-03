import { describe, expect, it, vi } from "vitest";
import {
  clearDynamicImportRecoveryReloadGuard,
  isRecoverableDynamicImportError,
  markDynamicImportRecoveryLoadSucceeded,
  maybeReloadForDynamicImportError,
} from "./dynamic-import-recovery";

describe("dynamic import recovery", () => {
  it("recognizes stale chunk load failures", () => {
    expect(
      isRecoverableDynamicImportError(
        new TypeError(
          "Failed to fetch dynamically imported module: https://paxai.app/assets/AxSettingsDialog-D13ooxHu.js",
        ),
      ),
    ).toBe(true);
    expect(
      isRecoverableDynamicImportError(
        new Error("ChunkLoadError: Loading chunk 9 failed."),
      ),
    ).toBe(true);
    expect(
      isRecoverableDynamicImportError(new Error("ordinary render failure")),
    ).toBe(false);
  });

  it("reloads once for a recoverable dynamic import failure", () => {
    const store = new Map<string, string>();
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    };
    const reload = vi.fn();
    const error = new TypeError(
      "Failed to fetch dynamically imported module: https://paxai.app/assets/AxSettingsDialog-D13ooxHu.js",
    );

    expect(
      maybeReloadForDynamicImportError(error, {
        storage,
        reload,
      }),
    ).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);

    expect(
      maybeReloadForDynamicImportError(error, {
        storage,
        reload,
      }),
    ).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("clears the reload guard after a settled successful boot", () => {
    const store = new Map<string, string>([
      ["ax:dynamic-import-reload-attempted", "1"],
    ]);
    const storage = {
      removeItem: (key: string) => {
        store.delete(key);
      },
    };
    const scheduled: Array<() => void> = [];

    markDynamicImportRecoveryLoadSucceeded({
      storage,
      delayMs: 25,
      setTimeout: (callback, delay) => {
        expect(delay).toBe(25);
        scheduled.push(callback);
      },
    });

    expect(store.get("ax:dynamic-import-reload-attempted")).toBe("1");
    expect(scheduled).toHaveLength(1);

    scheduled[0]();
    expect(store.has("ax:dynamic-import-reload-attempted")).toBe(false);
  });

  it("allows a future stale chunk reload after the successful boot cleanup", () => {
    const store = new Map<string, string>();
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    };
    const reload = vi.fn();
    const error = new Error("ChunkLoadError: Loading chunk 42 failed.");

    expect(maybeReloadForDynamicImportError(error, { storage, reload })).toBe(
      true,
    );
    expect(maybeReloadForDynamicImportError(error, { storage, reload })).toBe(
      false,
    );

    clearDynamicImportRecoveryReloadGuard(storage);

    expect(maybeReloadForDynamicImportError(error, { storage, reload })).toBe(
      true,
    );
    expect(reload).toHaveBeenCalledTimes(2);
  });
});
