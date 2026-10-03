/**
 * Environment-aware storage utilities
 * Prevents conflicts when running multiple environments on same domain
 */
import { config } from "../config/environment";
import { safeLocalStorage } from "./safe-local-storage";


export interface StoredSpace {
  id?: string;
  name?: string;
  slug?: string;
  visibility?: string;
  description?: string;
  member_count?: number;
  is_member?: boolean;
  is_current?: boolean;
  created_at?: string;
  is_personal?: boolean;
}

// Legacy type alias for backward compatibility
export type StoredOrganization = StoredSpace;

// Prefix all storage keys with environment
const getStorageKey = (key: string): string => {
  return `waystation_${config.environment}_${key}`;
};

const SESSION_FLAG_KEY = getStorageKey("has_session");
const AUTH_CONFIG_KEY = getStorageKey("auth_config");
const CURRENT_ORG_KEY = getStorageKey("currentOrganization");
const CURRENT_SPACE_KEY = getStorageKey("currentSpace");
const HAS_SENT_MESSAGE_KEY = getStorageKey("engagement_has_sent_message");
const HELP_NUDGE_DISMISSED_KEY = getStorageKey(
  "engagement_help_nudge_dismissed",
);
const NOTIFICATIONS_CLEARED_KEY = getStorageKey("notifications_cleared_at");
const ENGAGEMENT_EVENT = "ax:engagement-update";

const normalizeStoredSpace = (space: StoredSpace): StoredSpace => ({
  id: space.id,
  name: space.name,
  slug: space.slug,
  visibility: space.visibility ?? "private",
  description: space.description,
  member_count: space.member_count,
  is_member: space.is_member,
  is_current: space.is_current,
  created_at: space.created_at,
  is_personal: space.is_personal ?? false,
});

const readStoredSpace = (
  key: string,
  errorMessage: string,
): StoredSpace | null => {
  try {
    const raw = safeLocalStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as StoredSpace;
  } catch (error) {
    console.error(errorMessage, error);
    return null;
  }
};

const deriveSpaceKey = (space?: StoredSpace | null): string => {
  if (space?.id) return `space:${space.id}`;
  if (space?.slug) return `slug:${space.slug}`;
  return "global";
};

const spacesEqual = (
  a?: StoredSpace | null,
  b?: StoredSpace | null,
): boolean => {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return (
    (a.id ?? null) === (b.id ?? null) &&
    (a.slug ?? null) === (b.slug ?? null) &&
    (a.name ?? null) === (b.name ?? null) &&
    (a.visibility ?? null) === (b.visibility ?? null) &&
    (a.is_personal ?? null) === (b.is_personal ?? null)
  );
};

// Legacy organization functions for backward compatibility
const deriveOrganizationKey = (space?: StoredOrganization | null): string => {
  return deriveSpaceKey(space);
};

const organizationsEqual = (
  a?: StoredOrganization | null,
  b?: StoredOrganization | null,
): boolean => {
  return spacesEqual(a, b);
};

const dispatchSpaceChange = (space: StoredSpace | null) => {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent("spaces:current-changed", {
      detail: {
        space,
        organization: space,
        spaceKey: deriveSpaceKey(space),
      },
    }),
  );
};

const dispatchEngagementUpdate = (detail: {
  hasSentMessage?: boolean;
  helpNudgeDismissed?: boolean;
}) => {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(ENGAGEMENT_EVENT, { detail }));
};

// Auto-login state management
const autoLoginPromise: Promise<void> | null = null;
const autoLoginInProgress = false;

// Global singleflight refresh coordination - prevents multiple components
// from triggering simultaneous token refreshes (which causes 429s)
let globalRefreshPromise: Promise<boolean> | null = null;

const decodeJwtPayload = (token: string): Record<string, unknown> | null => {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;

    const base64Url = parts[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    return JSON.parse(atob(padded));
  } catch (error) {
    console.error("Error decoding JWT payload:", error);
    return null;
  }
};

// Access tokens stay within this browser tab; durable refresh credentials are
// HttpOnly cookies issued and rotated by the backend.
let accessTokenMemory: string | null = null;
const readAccessToken = (): string | null => {
  try { return window.sessionStorage.getItem(getStorageKey("userToken")) || accessTokenMemory; }
  catch { return accessTokenMemory; }
};
const writeAccessToken = (value: string | null): void => {
  accessTokenMemory = value;
  try {
    if (value) window.sessionStorage.setItem(getStorageKey("userToken"), value);
    else window.sessionStorage.removeItem(getStorageKey("userToken"));
  } catch { /* in-memory session works when tab storage is unavailable */ }
};

export const storage = {
  // Token management - using safeLocalStorage for persistence (GitHub SSO)
  getUserToken(): string | null {
    return readAccessToken();
  },

  // Auto-login helper for development (DISABLED - user is authenticated)
  async autoLogin(): Promise<void> {
    console.log("🔐 Auto-login disabled - user is already authenticated");
    return;
  },

  // Async method to get token
  async getUserTokenAsync(): Promise<string | null> {
    return this.getUserToken();
  },

  setUserToken(token: string): void {
    writeAccessToken(token);
  },

  removeUserToken(): void {
    writeAccessToken(null);
  },

  // Compatibility methods for older API callers. Refresh tokens are never
  // available to JavaScript in local-auth mode.
  getRefreshToken(): string | null { return null; },
  setRefreshToken(_token: string): void {},
  removeRefreshToken(): void {},
  getIdToken(): string | null { return null; },
  setIdToken(_token: string): void {},
  removeIdToken(): void {},

  // Set both tokens at once (for login)
  setTokens(accessToken: string, refreshToken: string): void {
    this.setUserToken(accessToken);
    // Store refresh token for Bearer authentication
    if (refreshToken) {
      this.setRefreshToken(refreshToken);
    }
    try {
      safeLocalStorage.setItem(SESSION_FLAG_KEY, "true");
    } catch {} // eslint-disable-line no-empty
  },

  // Clear all tokens (for logout)
  clearTokens(): void {
    this.removeUserToken();
    this.removeRefreshToken();
    this.removeIdToken();
    try {
      safeLocalStorage.removeItem(SESSION_FLAG_KEY);
    } catch {} // eslint-disable-line no-empty
    try {
      this.setCurrentOrganization?.(null);
    } catch {} // eslint-disable-line no-empty
  },

  // Token refresh functionality - supports both Bearer tokens and HttpOnly cookies
  // Uses global singleflight coordination to prevent 429s from concurrent refreshes
  async refreshTokens(): Promise<boolean> {
    // SINGLEFLIGHT: If a refresh is already in progress, wait for it
    if (globalRefreshPromise) {
      console.log("🔄 Refresh already in progress, waiting for result...");
      return globalRefreshPromise;
    }

    const refresh = () => this._doRefreshTokens();
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    globalRefreshPromise = locks
      ? locks.request("waystation-refresh-cookie", refresh)
      : refresh();
    const current = globalRefreshPromise;
    try { return await current; }
    finally { if (globalRefreshPromise === current) globalRefreshPromise = null; }
  },

  async _doRefreshTokens(): Promise<boolean> {
    try {
      const response = await fetch("/auth/local/refresh", {
        method: "POST", credentials: "include",
      });
      if (!response.ok) return false;
      const data = await response.json();
      if (!data.access_token || !data.user?.username) return false;
      this.setUserToken(data.access_token);
      this.setUsername(data.user.username);
      this.setUserMetadata(data.user);
      this.markSessionActive();
      window.dispatchEvent(new CustomEvent("auth:token-refreshed"));
      return true;
    } catch { return false; }
  },

  // Hint that we likely have a restorable session (prevents login flash)
  hasSessionHint(): boolean {
    try {
      const flag = safeLocalStorage.getItem(SESSION_FLAG_KEY) === "true";
      const rt = this.getRefreshToken();
      return !!(flag || rt);
    } catch {
      return false;
    }
  },

  // Check if access token is expired or near expiry
  isTokenExpired(token?: string | null, bufferSeconds: number = 60): boolean {
    try {
      const tokenToCheck = token || this.getUserToken();
      if (!tokenToCheck) return true;

      const payload = decodeJwtPayload(tokenToCheck);
      if (!payload.exp) return true;

      const now = Math.floor(Date.now() / 1000);
      // Consider token expired if it expires within bufferSeconds
      return Number(payload.exp) <= now + bufferSeconds;
    } catch (error) {
      console.error("Error checking token expiration:", error);
      return true; // Assume expired on error
    }
  },

  markSessionActive(): void {
    try {
      safeLocalStorage.setItem(SESSION_FLAG_KEY, "true");
    } catch {} // eslint-disable-line no-empty
  },

  hasSentMessage(): boolean {
    try {
      return safeLocalStorage.getItem(HAS_SENT_MESSAGE_KEY) === "1";
    } catch {
      return false;
    }
  },

  setHasSentMessage(value: boolean = true): void {
    try {
      if (value) {
        safeLocalStorage.setItem(HAS_SENT_MESSAGE_KEY, "1");
      } else {
        safeLocalStorage.removeItem(HAS_SENT_MESSAGE_KEY);
      }
    } catch {
      // eslint-disable-next-line no-empty
    }
    dispatchEngagementUpdate({ hasSentMessage: value });
  },

  hasHelpNudgeDismissed(): boolean {
    try {
      return safeLocalStorage.getItem(HELP_NUDGE_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  },

  setHelpNudgeDismissed(value: boolean = true): void {
    try {
      if (value) {
        safeLocalStorage.setItem(HELP_NUDGE_DISMISSED_KEY, "1");
      } else {
        safeLocalStorage.removeItem(HELP_NUDGE_DISMISSED_KEY);
      }
    } catch {
      // eslint-disable-next-line no-empty
    }
    dispatchEngagementUpdate({ helpNudgeDismissed: value });
  },

  getNotificationsClearedAt(orgId?: string | null): string | null {
    const scope = orgId ? `org:${orgId}` : "all";
    try {
      return safeLocalStorage.getItem(`${NOTIFICATIONS_CLEARED_KEY}:${scope}`);
    } catch {
      return null;
    }
  },

  setNotificationsClearedAt(timestamp: string, orgId?: string | null): void {
    const scope = orgId ? `org:${orgId}` : "all";
    try {
      safeLocalStorage.setItem(
        `${NOTIFICATIONS_CLEARED_KEY}:${scope}`,
        timestamp,
      );
    } catch {
      // eslint-disable-next-line no-empty
    }
  },

  // Device identity (stable per-browser) for per-device sessions
  getDeviceId(): string {
    const key = getStorageKey("deviceId");
    let id = safeLocalStorage.getItem(key);
    if (!id) {
      // Generate v4-ish UUID (sufficient for client identity)
      id = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
        /[xy]/g,
        function (c) {
          const r = (crypto.getRandomValues(new Uint8Array(1))[0] & 0xf) >> 0;
          const v = c === "x" ? r : (r & 0x3) | 0x8;
          return v.toString(16);
        },
      );
      safeLocalStorage.setItem(key, id);
    }
    return id;
  },

  // User info
  getUsername(): string | null {
    return safeLocalStorage.getItem(getStorageKey("username"));
  },

  setUsername(username: string): void {
    safeLocalStorage.setItem(getStorageKey("username"), username);
  },

  // User metadata (including admin status)
  getUserMetadata(): any {
    const metadata = safeLocalStorage.getItem(getStorageKey("userMetadata"));
    return metadata ? JSON.parse(metadata) : null;
  },

  setUserMetadata(metadata: any): void {
    safeLocalStorage.setItem(
      getStorageKey("userMetadata"),
      JSON.stringify(metadata),
    );
  },

  getCurrentOrganization(): StoredOrganization | null {
    return this.getSpace();
  },

  getCurrentOrgId(): string | null {
    return this.getCurrentSpaceId();
  },

  setCurrentOrganization(org: StoredOrganization | null): void {
    this.setSpace(org);
  },

  clearCurrentOrganization(): void {
    this.clearSpace();
  },

  getCurrentOrganizationKey(): string {
    return this.getCurrentSpaceKey();
  },

  // Space management (new API)
  getSpace(): StoredSpace | null {
    const storedSpace = readStoredSpace(
      CURRENT_SPACE_KEY,
      "Failed to parse stored space:",
    );
    if (storedSpace) {
      return storedSpace;
    }

    const legacySpace = readStoredSpace(
      CURRENT_ORG_KEY,
      "Failed to parse stored organization:",
    );
    if (legacySpace) {
      try {
        safeLocalStorage.setItem(
          CURRENT_SPACE_KEY,
          JSON.stringify(legacySpace),
        );
      } catch {
        // eslint-disable-next-line no-empty
      }
    }
    return legacySpace;
  },

  getCurrentSpaceId(): string | null {
    return this.getSpace()?.id ?? null;
  },

  setSpace(space: StoredSpace | null): void {
    try {
      const existing = this.getSpace();

      if (!space) {
        safeLocalStorage.removeItem(CURRENT_SPACE_KEY);
        safeLocalStorage.removeItem(CURRENT_ORG_KEY);
        if (existing) {
          dispatchSpaceChange(null);
        }
        return;
      }

      const normalized = normalizeStoredSpace(space);

      safeLocalStorage.setItem(CURRENT_SPACE_KEY, JSON.stringify(normalized));
      safeLocalStorage.setItem(CURRENT_ORG_KEY, JSON.stringify(normalized));

      if (!spacesEqual(existing, normalized)) {
        dispatchSpaceChange(normalized);
      }
    } catch (error) {
      console.error("Failed to store space:", error);
    }
  },

  clearSpace(): void {
    const existing = this.getSpace();
    try {
      safeLocalStorage.removeItem(CURRENT_SPACE_KEY);
      safeLocalStorage.removeItem(CURRENT_ORG_KEY);
    } catch {} // eslint-disable-line no-empty
    if (existing) {
      dispatchSpaceChange(null);
    }
  },

  getCurrentSpaceKey(): string {
    return deriveSpaceKey(this.getSpace());
  },

  // Clear all environment-specific data
  clearAll(): void {
    this.clearTokens();
    try {
      this.setCurrentOrganization?.(null);
    } catch {} // eslint-disable-line no-empty
    const prefix = `waystation_${config.environment}_`;

    // Use safeLocalStorage.keys() to honor memory fallback in SSR/Safari private mode
    const keysToRemove = safeLocalStorage.keys(prefix);

    // Remove all matching keys
    keysToRemove.forEach((key) => safeLocalStorage.removeItem(key));
    console.log(
      `🗑️ Cleared ${keysToRemove.length} items from storage on logout`,
    );
  },

  // Widget visibility preferences (per-space, frontend-only)
  // Returns null when the user has never customized (caller should use defaults).
  getHiddenWidgets(spaceId?: string | null): Set<string> | null {
    const key = getStorageKey(`hidden_widgets:${spaceId || "global"}`);
    try {
      const raw = safeLocalStorage.getItem(key);
      return raw ? new Set(JSON.parse(raw) as string[]) : null;
    } catch {
      return null;
    }
  },

  setHiddenWidgets(hidden: Set<string>, spaceId?: string | null): void {
    const key = getStorageKey(`hidden_widgets:${spaceId || "global"}`);
    try {
      safeLocalStorage.setItem(key, JSON.stringify([...hidden]));
    } catch {
      // eslint-disable-next-line no-empty
    }
  },

  // Debug helper
  debugStorage(): void {
    console.log(`🗄️ Storage for ${config.environment}:`);
    const prefix = `waystation_${config.environment}_`;
    const items = Object.keys(safeLocalStorage)
      .filter((key) => key.startsWith(prefix))
      .map((key) => {
        const value = safeLocalStorage.getItem(key);
        const truncated = key.includes("Token")
          ? `${value?.substring(0, 20)}...`
          : value?.substring(0, 50);
        return `  ${key}: ${truncated}`;
      });

    if (items.length === 0) {
      console.log("  (no items found)");
    } else {
      items.forEach((item) => console.log(item));
    }

    // Show token status
    console.log(`🔑 Token status:`);
    console.log(
      `  Access token: ${this.getUserToken() ? "Present" : "Missing"}`,
    );
    console.log(
      `  Refresh token: ${this.getRefreshToken() ? "Present" : "Missing"}`,
    );
  },
};

// Do not import tokens from the original application's persistent storage.
export function migrateStorage(): void {}
export async function initializeTestUser(): Promise<void> {}

export { safeLocalStorage };
