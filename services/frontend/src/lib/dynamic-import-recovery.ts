const DYNAMIC_IMPORT_RELOAD_KEY = "ax:dynamic-import-reload-attempted";
const DYNAMIC_IMPORT_RECOVERY_SETTLE_MS = 10_000;

type DynamicImportRecoveryStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>;

export function isRecoverableDynamicImportError(error: unknown): boolean {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "";
  if (!message) return false;
  return (
    message.includes("Failed to fetch dynamically imported module") ||
    message.includes("Importing a module script failed") ||
    message.includes("ChunkLoadError")
  );
}

export function maybeReloadForDynamicImportError(
  error: unknown,
  opts?: {
    storage?: DynamicImportRecoveryStorage;
    reload?: () => void;
  },
): boolean {
  if (!isRecoverableDynamicImportError(error)) {
    return false;
  }

  const storage =
    opts?.storage ??
    (typeof window !== "undefined" && window.sessionStorage
      ? window.sessionStorage
      : null);
  const reload =
    opts?.reload ??
    (typeof window !== "undefined" ? () => window.location.reload() : null);

  if (!storage || !reload) {
    return false;
  }

  if (storage.getItem(DYNAMIC_IMPORT_RELOAD_KEY) === "1") {
    return false;
  }

  storage.setItem(DYNAMIC_IMPORT_RELOAD_KEY, "1");
  reload();
  return true;
}

export function clearDynamicImportRecoveryReloadGuard(
  storage?: Pick<Storage, "removeItem"> | null,
): void {
  const targetStorage =
    storage ??
    (typeof window !== "undefined" && window.sessionStorage
      ? window.sessionStorage
      : null);

  if (!targetStorage) return;

  try {
    targetStorage.removeItem(DYNAMIC_IMPORT_RELOAD_KEY);
  } catch {
    // Best-effort cleanup only. If storage is unavailable, keeping the guard is
    // safer than throwing during application boot.
  }
}

export function markDynamicImportRecoveryLoadSucceeded(opts?: {
  storage?: Pick<Storage, "removeItem"> | null;
  setTimeout?: (callback: () => void, delay: number) => unknown;
  delayMs?: number;
}): void {
  const schedule =
    opts?.setTimeout ??
    (typeof window !== "undefined" ? window.setTimeout.bind(window) : null);

  if (!schedule) return;

  schedule(
    () => clearDynamicImportRecoveryReloadGuard(opts?.storage),
    opts?.delayMs ?? DYNAMIC_IMPORT_RECOVERY_SETTLE_MS,
  );
}
