/**
 * Token Refresh Hook
 * Proactively refreshes tokens before they expire to prevent 401 errors
 */

import { useEffect, useRef } from "react";
import { storage } from "../lib/storage";

interface TokenPayload {
  exp: number;
  iat: number;
  sub: string;
}

function parseJwt(token: string): TokenPayload | null {
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join(""),
    );
    return JSON.parse(jsonPayload);
  } catch (e) {
    console.error("Failed to parse JWT:", e);
    return null;
  }
}

export function useTokenRefresh() {
  const refreshTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isRefreshingRef = useRef(false);

  const scheduleRefresh = (token: string) => {
    // Clear any existing timeout
    if (refreshTimeoutRef.current) {
      clearTimeout(refreshTimeoutRef.current);
      refreshTimeoutRef.current = null;
    }

    const payload = parseJwt(token);
    if (!payload) {
      console.error("❌ Could not parse token for refresh scheduling");
      return;
    }

    const now = Math.floor(Date.now() / 1000);
    const expiresIn = payload.exp - now;

    // Refresh when 75% of token lifetime has passed
    // For 2 min tokens = 90 seconds, for 15 min = 11.25 minutes
    const refreshIn = Math.floor(expiresIn * 0.75);

    if (refreshIn <= 0) {
      console.log(
        "⚠️ Token already expired or expiring soon, refreshing immediately",
      );
      performRefresh();
      return;
    }

    const refreshTime = new Date(Date.now() + refreshIn * 1000);
    console.log(
      `⏰ Scheduling token refresh in ${refreshIn}s (at ${refreshTime.toLocaleTimeString()})`,
    );
    console.log(`  Current time: ${new Date().toLocaleTimeString()}`);
    console.log(
      `  Token expires: ${new Date(payload.exp * 1000).toLocaleTimeString()}`,
    );
    console.log(
      `  Refresh at: ${refreshTime.toLocaleTimeString()} (${Math.floor(refreshIn / 60)}m ${refreshIn % 60}s from now)`,
    );

    refreshTimeoutRef.current = setTimeout(() => {
      performRefresh();
    }, refreshIn * 1000);
  };

  const performRefresh = async () => {
    if (isRefreshingRef.current) {
      console.log("🔄 Already refreshing, skipping duplicate request");
      return;
    }

    isRefreshingRef.current = true;
    console.log(
      `🔄 [TokenRefresh] Proactive refresh started at ${new Date().toLocaleTimeString()}`,
    );

    try {
      const success = await storage.refreshTokens();
      if (success) {
        const newToken = storage.getUserToken();
        if (newToken) {
          console.log("✅ [TokenRefresh] Proactive refresh successful");
          // Schedule next refresh
          scheduleRefresh(newToken);

          // Dispatch custom event for other components (like SSE)
          window.dispatchEvent(
            new CustomEvent("auth:token-refreshed", {
              detail: { proactive: true, timestamp: Date.now() },
            }),
          );
        }
      } else {
        console.error("❌ [TokenRefresh] Proactive refresh failed");
      }
    } catch (error) {
      console.error("❌ [TokenRefresh] Refresh error:", error);
    } finally {
      isRefreshingRef.current = false;
    }
  };

  useEffect(() => {
    // Initial setup - only schedule if we have a token
    const token = storage.getUserToken();
    if (token) {
      scheduleRefresh(token);
    } else {
      console.log(
        "⏸️ [TokenRefresh] No token present, relying on AuthContext session bootstrap",
      );
    }

    // Listen for token changes (e.g., from login or manual refresh)
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key?.includes("userToken") && e.newValue) {
        console.log("🔄 Token changed in storage, rescheduling refresh");
        scheduleRefresh(e.newValue);
      }
    };

    // Listen for auth events
    const handleTokenRefreshed = (e: CustomEvent) => {
      if (!e.detail?.proactive) {
        // If this wasn't our proactive refresh, reschedule
        const token = storage.getUserToken();
        if (token) {
          console.log("🔄 Token refreshed externally, rescheduling");
          scheduleRefresh(token);
        }
      }
    };

    // Handle visibility changes (tab becomes visible)
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        console.log("👁️ Tab became visible, checking token status...");
        const token = storage.getUserToken();
        if (token) {
          // Use the new isTokenExpired helper for consistent expiry checking
          if (storage.isTokenExpired(token, 300)) {
            // 5 min buffer
            console.log(
              "⏰ Token expired/near expiry on visibility change, refreshing...",
            );
            performRefresh();
          }
        }

        // Also trigger SSE reconnect if needed
        window.dispatchEvent(new CustomEvent("visibility:resumed"));
      }
    };

    // Handle online event (network reconnected)
    const handleOnline = () => {
      console.log("🌐 Back online, checking token and SSE...");
      const token = storage.getUserToken();
      if (token) {
        // Use isTokenExpired with 10 min buffer for online event
        if (storage.isTokenExpired(token, 600)) {
          console.log("⏰ Token needs refresh after coming online");
          performRefresh();
        }
      }

      // Trigger SSE reconnect
      window.dispatchEvent(new CustomEvent("network:online"));
    };

    window.addEventListener("storage", handleStorageChange);
    window.addEventListener(
      "auth:token-refreshed",
      handleTokenRefreshed as EventListener,
    );
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("online", handleOnline);

    return () => {
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current);
      }
      window.removeEventListener("storage", handleStorageChange);
      window.removeEventListener(
        "auth:token-refreshed",
        handleTokenRefreshed as EventListener,
      );
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("online", handleOnline);
    };
  }, []);

  return { performRefresh };
}
