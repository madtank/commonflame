import { useEffect, useMemo, useState } from "react";

import { storage } from "./storage";

type AuthenticatedMediaState = {
  src: string;
  requiresAuth: boolean;
  loading: boolean;
  error: string | null;
};

const isBrowser = () => typeof window !== "undefined";

export function requiresAuthenticatedMediaFetch(rawUrl: string): boolean {
  if (!rawUrl || !isBrowser()) return false;
  if (
    rawUrl.startsWith("blob:") ||
    rawUrl.startsWith("data:") ||
    rawUrl.startsWith("javascript:")
  ) {
    return false;
  }

  try {
    const parsed = new URL(rawUrl, window.location.origin);
    return (
      parsed.origin === window.location.origin &&
      parsed.pathname.startsWith("/api/")
    );
  } catch {
    return false;
  }
}

export async function fetchAuthenticatedMediaBlobUrl(
  rawUrl: string,
): Promise<string> {
  const token = await storage.getUserTokenAsync();
  const headers: HeadersInit = token
    ? { Authorization: `Bearer ${token}` }
    : {};
  const response = await fetch(rawUrl, {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new Error(`Attachment fetch failed (${response.status})`);
  }
  return URL.createObjectURL(await response.blob());
}

export async function fetchAuthenticatedMediaText(
  rawUrl: string,
): Promise<string> {
  const token = await storage.getUserTokenAsync();
  const headers: HeadersInit = token
    ? { Authorization: `Bearer ${token}` }
    : {};
  const response = await fetch(rawUrl, {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new Error(`Attachment fetch failed (${response.status})`);
  }
  return response.text();
}

export function useAuthenticatedMediaUrl(
  rawUrl: string,
  options: { enabled?: boolean } = {},
): AuthenticatedMediaState {
  const enabled = options.enabled ?? true;
  const requiresAuth = useMemo(
    () => enabled && requiresAuthenticatedMediaFetch(rawUrl),
    [enabled, rawUrl],
  );
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!requiresAuth) {
      setObjectUrl(null);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    let nextObjectUrl: string | null = null;
    setLoading(true);
    setError(null);

    fetchAuthenticatedMediaBlobUrl(rawUrl)
      .then((value) => {
        nextObjectUrl = value;
        if (cancelled) {
          URL.revokeObjectURL(value);
          return;
        }
        setObjectUrl(value);
      })
      .catch((err) => {
        if (!cancelled) {
          setObjectUrl(null);
          setError(
            err instanceof Error ? err.message : "Attachment fetch failed",
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      if (nextObjectUrl) {
        URL.revokeObjectURL(nextObjectUrl);
      }
    };
  }, [rawUrl, requiresAuth]);

  return {
    src: objectUrl || (!requiresAuth ? rawUrl : ""),
    requiresAuth,
    loading,
    error,
  };
}

export async function openAuthenticatedMediaUrl(rawUrl: string): Promise<void> {
  if (!requiresAuthenticatedMediaFetch(rawUrl)) {
    window.open(rawUrl, "_blank", "noopener,noreferrer");
    return;
  }

  const pendingWindow = window.open("", "_blank");
  if (pendingWindow) {
    pendingWindow.opener = null;
  }
  try {
    const objectUrl = await fetchAuthenticatedMediaBlobUrl(rawUrl);
    if (pendingWindow) {
      pendingWindow.location.href = objectUrl;
    } else {
      window.open(objectUrl, "_blank", "noopener,noreferrer");
    }
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  } catch (error) {
    pendingWindow?.close();
    throw error;
  }
}
