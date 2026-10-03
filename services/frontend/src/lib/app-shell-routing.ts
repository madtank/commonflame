export const MODERN_APP_PATH = "/app";
export const LEGACY_APP_PATH = "/legacy";
export const MODERN_ADMIN_PATH = "/admin";
export const LOGIN_PATH = "/login";

const MODERN_APP_PATHS = new Set([
  MODERN_APP_PATH,
  "/ax",
  "/ax/",
  `${MODERN_APP_PATH}/`,
  "/space-agent",
  "/space-agent/",
]);

const MODERN_STANDALONE_PATHS = new Set([
  MODERN_ADMIN_PATH,
  `${MODERN_ADMIN_PATH}/`,
]);

const RETIRED_LEGACY_PATHS = new Set([
  LEGACY_APP_PATH,
  `${LEGACY_APP_PATH}/`,
  "/home",
  "/home/",
]);
const LOGIN_PATHS = new Set([LOGIN_PATH, `${LOGIN_PATH}/`]);
const REQUEST_ACCESS_PATHS = new Set(["/request-access", "/request-access/"]);

type AppShellRoutingOptions = {
  path: string;
  uiMode?: string | null;
  appVariant?: string | null;
};

type RequestAccessRoutingOptions = {
  path: string;
  hasUserToken: boolean;
};

// Fresh auth should always land on the explicit modern product shell. The root
// path is reserved for the public marketing page.
export const getPostLoginRedirectPath = () => MODERN_APP_PATH;

export const isPublicMarketingPath = (path: string) =>
  path === "/" || path === "";

export const isRequestAccessPath = (path: string) =>
  REQUEST_ACCESS_PATHS.has(path);

// Open signup retired the approval gate: any signed-in visitor on the
// request-access URL goes straight to the app; signed-out visitors see the
// public marketing page like any other non-login path.
export const shouldRedirectAuthenticatedUserFromRequestAccess = ({
  path,
  hasUserToken,
}: RequestAccessRoutingOptions) => isRequestAccessPath(path) && hasUserToken;

export const shouldShowPublicLandingForUnauthenticatedPath = (path: string) =>
  !isExplicitLoginPath(path);

export const isRetiredLegacyPath = (path: string) =>
  RETIRED_LEGACY_PATHS.has(path);

// Legacy shell URLs are no longer production app routes. Keep this predicate
// false so shared routing/realtime guards cannot silently revive the old shell;
// historical archives must live outside the public SPA.
export const isExplicitLegacyPath = (_path: string) => false;

export const isExplicitLoginPath = (path: string) => LOGIN_PATHS.has(path);

export const isModernStandalonePath = (path: string) =>
  MODERN_STANDALONE_PATHS.has(path);

export const shouldRenderModernAppShell = ({
  path,
  uiMode,
  appVariant,
}: AppShellRoutingOptions) => {
  if (appVariant === "space-agent" || appVariant === "ax-platform") {
    return true;
  }

  if (MODERN_APP_PATHS.has(path)) {
    return true;
  }

  if (isExplicitLegacyPath(path)) {
    return false;
  }

  if (isPublicMarketingPath(path)) {
    return false;
  }

  return false;
};

export const shouldConnectLegacyRealtime = (
  options: AppShellRoutingOptions,
) => {
  if (isRetiredLegacyPath(options.path)) {
    return false;
  }

  if (isPublicMarketingPath(options.path)) {
    return false;
  }

  if (isModernStandalonePath(options.path)) {
    return false;
  }

  return false;
};
