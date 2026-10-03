/**
 * Safe localStorage wrapper that gracefully falls back to in-memory storage
 * when the browser blocks access (e.g. Safari private mode or third-party
 * cookie restrictions). This prevents runtime exceptions that would otherwise
 * force the app back to the login screen.
 */

type StorageStatus = "unknown" | "native" | "memory";

const memoryStore = new Map<string, string>();
let storageStatus: StorageStatus = "unknown";

const TEST_KEY = "__ax_storage_probe__";

let nativeStorage: Storage | null = null;
const nativeFns: Partial<Record<"getItem" | "setItem" | "removeItem" | "clear" | "key", (...args: any[]) => any>> = {};
let nativeLengthGetter: (() => number) | null = null;

const probeNativeStorage = (): Storage | null => {
  if (typeof window === "undefined") {
    storageStatus = "memory";
    nativeStorage = null;
    return null;
  }

  if (storageStatus === "native" && nativeStorage) {
    return nativeStorage;
  }

  // If native methods already captured (before shimming), reuse them instead of re-binding
  // This prevents capturing shimmed methods after __resetForTests() is called
  if (nativeFns.setItem && nativeFns.getItem && nativeFns.removeItem) {
    try {
      const storage = window.localStorage;
      // Test that the captured methods still work
      nativeFns.setItem(TEST_KEY, "1");
      nativeFns.removeItem(TEST_KEY);
      nativeStorage = storage;
      storageStatus = "native";
      return nativeStorage;
    } catch (error) {
      nativeStorage = null;
      markMemoryFallback(error);
      return null;
    }
  }

  try {
    const storage = window.localStorage;
    // Get methods - they might be on prototype in jsdom
    const getItem = storage.getItem || Storage.prototype.getItem;
    const setItem = storage.setItem || Storage.prototype.setItem;
    const removeItem = storage.removeItem || Storage.prototype.removeItem;
    const clear = storage.clear || Storage.prototype.clear;
    const key = storage.key || Storage.prototype.key;

    if (!getItem || !setItem || !removeItem || !clear || !key) {
      throw new Error("localStorage methods missing");
    }

    // Bind native methods so we can safely call them even if we override later
    nativeFns.getItem = getItem.bind(storage);
    nativeFns.setItem = setItem.bind(storage);
    nativeFns.removeItem = removeItem.bind(storage);
    nativeFns.clear = clear.bind(storage);
    nativeFns.key = key.bind(storage);

    const proto = Object.getPrototypeOf(storage) ?? Storage.prototype;
    const lengthDescriptor = Object.getOwnPropertyDescriptor(proto, "length");
    if (lengthDescriptor?.get) {
      nativeLengthGetter = lengthDescriptor.get.bind(storage);
    } else {
      nativeLengthGetter = () => (storage as Storage).length;
    }

    // Probe write access (Safari private mode throws here)
    nativeFns.setItem(TEST_KEY, "1");
    nativeFns.removeItem(TEST_KEY);

    nativeStorage = storage;
    storageStatus = "native";
    return nativeStorage;
  } catch (error) {
    nativeStorage = null;
    markMemoryFallback(error);
    return null;
  }
};

const normaliseKey = (key: string): string => String(key);

const memory = {
  getItem(key: string): string | null {
    return memoryStore.has(key) ? memoryStore.get(key)! : null;
  },
  setItem(key: string, value: string): void {
    memoryStore.set(normaliseKey(key), String(value));
  },
  removeItem(key: string): void {
    memoryStore.delete(normaliseKey(key));
  },
  clear(): void {
    memoryStore.clear();
  },
  key(index: number): string | null {
    const keys = Array.from(memoryStore.keys());
    return keys[index] ?? null;
  },
  length(): number {
    return memoryStore.size;
  },
  keys(prefix?: string): string[] {
    const keys = Array.from(memoryStore.keys());
    if (!prefix) return keys;
    return keys.filter((k) => k.startsWith(prefix));
  },
};

const markMemoryFallback = (reason?: unknown) => {
    if (storageStatus !== "memory" && typeof console !== "undefined" && reason) {
      console.warn(
        "📦 localStorage unavailable – using in-memory fallback. Session will persist via refresh tokens only.",
        reason,
      );
    }
    storageStatus = "memory";
};

const getNativeStorage = (): Storage | null => {
  if (storageStatus === "memory") {
    return null;
  }

  return probeNativeStorage();
};

const safeGetItem = (key: string): string | null => {
  const storage = getNativeStorage();
  if (storage) {
    try {
      // jsdom's localStorage doesn't work with .bind(), use direct calls in test mode
      const isTest = typeof process !== 'undefined' && process.env?.NODE_ENV === 'test';
      const result = (isTest || !nativeFns.getItem) ? storage.getItem(key) : nativeFns.getItem(key);
      return result;
    } catch (error) {
      markMemoryFallback(error);
    }
  }
  const memResult = memory.getItem(key);
  return memResult;
};

const safeSetItem = (key: string, value: string): void => {
  const storage = getNativeStorage();
  if (storage) {
    try {
      // jsdom's localStorage doesn't work with .bind(), use direct calls in test mode
      const isTest = typeof process !== 'undefined' && process.env?.NODE_ENV === 'test';
      if (isTest || !nativeFns.setItem) {
        storage.setItem(key, value);
        // Verify write
        const verify = storage.getItem(key);
        const directVerify = window.localStorage.getItem(key);
      } else {
        nativeFns.setItem(key, value);
      }
      return;
    } catch (error) {
      markMemoryFallback(error);
    }
  }
  memory.setItem(key, value);
};

const safeRemoveItem = (key: string): void => {
  const storage = getNativeStorage();
  if (storage) {
    try {
      // jsdom's localStorage doesn't work with .bind(), use direct calls in test mode
      const isTest = typeof process !== 'undefined' && process.env?.NODE_ENV === 'test';
      if (isTest || !nativeFns.removeItem) {
        storage.removeItem(key);
      } else {
        nativeFns.removeItem(key);
      }
      return;
    } catch (error) {
      markMemoryFallback(error);
    }
  }
  memory.removeItem(key);
};

const safeClear = (): void => {
  const storage = getNativeStorage();
  if (storage) {
    try {
      if (nativeFns.clear) {
        nativeFns.clear();
      } else {
        storage.clear();
      }
      return;
    } catch (error) {
      markMemoryFallback(error);
    }
  }
  memory.clear();
};

const safeKey = (index: number): string | null => {
  const storage = getNativeStorage();
  if (storage) {
    try {
      if (nativeFns.key) {
        return nativeFns.key(index);
      }
      return storage.key(index);
    } catch (error) {
      markMemoryFallback(error);
    }
  }
  return memory.key(index);
};

const safeLength = (): number => {
  if (storageStatus === "native" && nativeLengthGetter) {
    try {
      return nativeLengthGetter();
    } catch (error) {
      markMemoryFallback(error);
    }
  } else {
    getNativeStorage();
    if (storageStatus === "native" && nativeLengthGetter) {
      try {
        return nativeLengthGetter();
      } catch (error) {
        markMemoryFallback(error);
      }
    }
  }
  return memory.length();
};

const safeKeys = (prefix?: string): string[] => {
  const storage = getNativeStorage();
  if (storage) {
    try {
      const keys = Object.keys(storage);
      if (!prefix) return keys;
      return keys.filter((key) => key.startsWith(prefix));
    } catch (error) {
      markMemoryFallback(error);
    }
  }
  return memory.keys(prefix);
};

export const safeLocalStorage = {
  getItem: safeGetItem,
  setItem: safeSetItem,
  removeItem: safeRemoveItem,
  clear: safeClear,
  key: safeKey,
  keys: safeKeys,
  get length(): number {
    return safeLength();
  },
  /**
   * Returns true when we are storing data in-memory because the browser
   * rejected localStorage access. Useful for diagnostics and tests.
   */
  isUsingMemory(): boolean {
    return storageStatus === "memory";
  },
  /** Reset storage status for unit tests */
  __resetForTests(): void {
    storageStatus = "unknown";
    memoryStore.clear();
    nativeStorage = null;
    // Don't clear nativeFns - they're the original native methods captured before shimming
    // Clearing them causes re-probe to capture shimmed methods, creating infinite recursion
    // nativeLengthGetter is tied to nativeFns, so keep it too
  },
};

export type SafeLocalStorage = typeof safeLocalStorage;

// Ensure native references captured immediately in environments where code runs
probeNativeStorage();

// Provide a graceful shim so legacy direct localStorage usage doesn't crash
// IMPORTANT: Skip shimming in test environments to avoid infinite recursion
const installGlobalShim = () => {
  // Skip in test environments - tests need access to real localStorage
  if (typeof process !== 'undefined' && process.env?.NODE_ENV === 'test') {
    return;
  }
  if (typeof window === "undefined" || !window.localStorage) return;

  const define = (name: keyof Storage, value: any) => {
    try {
      Object.defineProperty(window.localStorage, name, {
        value,
        configurable: true,
        writable: true,
      });
    } catch {
      try {
        (window.localStorage as any)[name] = value;
      } catch {
        /* ignore */
      }
    }
  };

  define("getItem", (key: string) => safeGetItem(String(key)));
  define("setItem", (key: string, value: string) => {
    safeSetItem(String(key), String(value));
  });
  define("removeItem", (key: string) => {
    safeRemoveItem(String(key));
  });
  define("clear", () => {
    safeClear();
  });
  define("key", (index: number) => safeKey(Number(index)));

  try {
    Object.defineProperty(window.localStorage, "length", {
      get: () => safeLength(),
      configurable: true,
    });
  } catch {
    // Unable to override length - acceptable
  }
};

installGlobalShim();
