type WindowLocationLike = {
  origin: string;
  protocol: string;
  hostname: string;
};

type ResolveDirectUrlOptions = {
  envUrl?: string | null;
  isDev?: boolean;
  windowLocation?: WindowLocationLike | null;
  defaultProdOrigin?: string;
  devPort?: string;
};

export function normalizeApiOrigin(value: string): string {
  // Preserve explicit deployment mounts. The EC2 dev/staging host exposes the
  // backend behind `/api`, while next/prod use same-origin `/api/v1/*`.
  // Stripping `/api` makes staging call routes that do not exist.
  return value.replace(/\/+$/, "");
}

export function normalizeApiBaseUrl(value: string): string {
  // Browser API callers already pass route constants such as `/api/v1/spaces`
  // and `/auth/me`. Dev/staging may still configure VITE_API_URL with a
  // trailing `/api` mount for direct/backend callers; using that as the axios
  // base URL doubles routes into `/api/api/...` and breaks session bootstrap.
  return value.replace(/\/+$/, "").replace(/\/api$/, "");
}

export function isLoopbackHostname(
  hostname: string | null | undefined,
): boolean {
  if (!hostname) return false;
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return (
    normalized === "localhost" ||
    normalized === "127.0.0.1" ||
    normalized === "::1"
  );
}

export function isCloudFrontHostname(
  hostname: string | null | undefined,
): boolean {
  return (hostname || "").toLowerCase().endsWith(".cloudfront.net");
}

export function resolveDirectUrl({
  envUrl,
  isDev = false,
  windowLocation = typeof window !== "undefined" ? window.location : null,
  defaultProdOrigin = "",
  devPort,
}: ResolveDirectUrlOptions = {}): string {
  const trimmed = envUrl?.trim();
  if (trimmed) {
    return normalizeApiOrigin(trimmed);
  }

  if (!windowLocation) {
    return defaultProdOrigin;
  }





  return windowLocation.origin || defaultProdOrigin;
}

export function resolveDirectApiUrl(
  options: Omit<ResolveDirectUrlOptions, "devPort"> = {},
): string {
  return resolveDirectUrl({ ...options, devPort: "8001" });
}

export function resolveDirectMcpUrl(
  options: Omit<ResolveDirectUrlOptions, "devPort"> = {},
): string {
  return resolveDirectUrl({ ...options, devPort: "8002" });
}

export function resolveApiBaseUrl({
  envUrl,
  isDev = false,
  windowLocation = typeof window !== "undefined" ? window.location : null,
  defaultProdOrigin = "",
}: Omit<ResolveDirectUrlOptions, "devPort"> = {}): string {
  if (isDev) {
    return "";
  }

  const trimmed = envUrl?.trim();
  if (trimmed) {
    return normalizeApiBaseUrl(trimmed);
  }

  if (!windowLocation) {
    return defaultProdOrigin;
  }



  return windowLocation.origin || defaultProdOrigin;
}

export function isRemoteLoopbackApiTarget(
  apiUrl: string,
  pageOrigin = typeof window !== "undefined" ? window.location.origin : null,
): boolean {
  try {
    // When `pageOrigin` is unavailable (SSR/tests), resolve against localhost
    // and conservatively treat loopback-only targets as unusable for hosted pages.
    const targetUrl = new URL(apiUrl, pageOrigin ?? "http://localhost");
    if (!isLoopbackHostname(targetUrl.hostname)) {
      return false;
    }

    if (!pageOrigin) {
      return true;
    }

    const pageUrl = new URL(pageOrigin);
    return !isLoopbackHostname(pageUrl.hostname);
  } catch {
    return false;
  }
}

export function getSseLatencyMs(
  serverTime: string | null | undefined,
  nowMs = Date.now(),
  maxFutureSkewMs = 30000,
): number | null {
  if (!serverTime) return null;

  const serverTs = new Date(serverTime).getTime();
  if (!Number.isFinite(serverTs)) {
    return null;
  }

  const latencyMs = nowMs - serverTs;
  if (latencyMs < -maxFutureSkewMs) {
    return null;
  }

  return Math.max(0, latencyMs);
}
