/**
 * useFeatureFlags — fetches server-side flags from /api/v1/flags/
 * and merges with localStorage overrides.
 *
 * Server flags take precedence. localStorage is the offline fallback.
 * Caches in sessionStorage to avoid repeated fetches.
 */

import { useState, useEffect } from "react";
import { config } from "@/config/environment";
import { storage } from "@/lib/storage";

const CACHE_KEY = "ax_flags_cache";
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

interface FlagsCache {
  flags: Record<string, boolean>;
  ts: number;
}

function getLocalFlag(key: string): boolean | undefined {
  const val = localStorage.getItem(key);
  if (val === "true") return true;
  if (val === "false") return false;
  return undefined;
}

function getCachedFlags(): Record<string, boolean> | null {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const cached: FlagsCache = JSON.parse(raw);
    if (Date.now() - cached.ts > CACHE_TTL_MS) return null;
    return cached.flags;
  } catch {
    return null;
  }
}

function setCachedFlags(flags: Record<string, boolean>) {
  try {
    sessionStorage.setItem(
      CACHE_KEY,
      JSON.stringify({ flags, ts: Date.now() } satisfies FlagsCache),
    );
  } catch {
    // sessionStorage full or unavailable — ignore
  }
}

export function useFeatureFlags() {
  const [flags, setFlags] = useState<Record<string, boolean>>(() => {
    // Start with cached flags or empty
    return getCachedFlags() ?? {};
  });
  const [loaded, setLoaded] = useState(() => getCachedFlags() !== null);

  useEffect(() => {
    let cancelled = false;

    async function fetchFlags() {
      try {
        const token =
          (await storage.getUserTokenAsync?.()) ??
          localStorage.getItem("token");
        const apiUrl = config.apiUrl;

        const res = await fetch(`${apiUrl}/api/v1/flags/`, {
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        });

        if (!res.ok) return;

        const data = (await res.json()) as { flags: Record<string, boolean> };
        if (cancelled) return;

        setCachedFlags(data.flags);
        setFlags(data.flags);
        setLoaded(true);
      } catch {
        // Network error — fall back to localStorage
        setLoaded(true);
      }
    }

    fetchFlags();
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Get a flag value. Priority: server → localStorage → defaultValue.
   */
  function getFlag(name: string, defaultValue = false): boolean {
    // Server flag takes precedence
    if (name in flags) return flags[name];
    // localStorage override
    const local = getLocalFlag(name);
    if (local !== undefined) return local;
    return defaultValue;
  }

  /**
   * Toggle a flag via PUT /api/v1/flags/{name}?enabled={value}.
   * Updates local cache immediately for instant UI feedback.
   */
  async function setFlag(name: string, enabled: boolean): Promise<boolean> {
    try {
      const token =
        (await storage.getUserTokenAsync?.()) ?? localStorage.getItem("token");
      const apiUrl = config.apiUrl;

      const res = await fetch(
        `${apiUrl}/api/v1/flags/${encodeURIComponent(name)}?enabled=${enabled}`,
        {
          method: "PUT",
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        },
      );

      if (!res.ok) return false;

      // Update local state + cache
      const updated = { ...flags, [name]: enabled };
      setFlags(updated);
      setCachedFlags(updated);
      return true;
    } catch {
      return false;
    }
  }

  return { flags, loaded, getFlag, setFlag };
}

/**
 * Standalone (non-hook) flag check for use outside React components.
 * Checks sessionStorage cache first, then localStorage.
 */
export function getFeatureFlag(name: string, defaultValue = false): boolean {
  const cached = getCachedFlags();
  if (cached && name in cached) return cached[name];
  const local = getLocalFlag(name);
  if (local !== undefined) return local;
  return defaultValue;
}
