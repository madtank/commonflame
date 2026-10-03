import {
  LEGACY_APP_PATH,
  MODERN_ADMIN_PATH,
  MODERN_APP_PATH,
  getPostLoginRedirectPath,
  isExplicitLegacyPath,
  isExplicitLoginPath,
  isModernStandalonePath,
  isRequestAccessPath,
  isRetiredLegacyPath,
  shouldConnectLegacyRealtime,
  shouldRedirectAuthenticatedUserFromRequestAccess,
  shouldRenderModernAppShell,
  shouldShowPublicLandingForUnauthenticatedPath,
} from "./app-shell-routing";

describe("app shell routing", () => {
  it("sends signed-in request-access visitors to the app, signed-out to marketing", () => {
    expect(isRequestAccessPath("/request-access")).toBe(true);
    expect(isRequestAccessPath("/request-access/")).toBe(true);
    expect(isRequestAccessPath("/ax")).toBe(false);

    expect(
      shouldRedirectAuthenticatedUserFromRequestAccess({
        path: "/request-access",
        hasUserToken: true,
      }),
    ).toBe(true);

    expect(
      shouldRedirectAuthenticatedUserFromRequestAccess({
        path: "/request-access",
        hasUserToken: false,
      }),
    ).toBe(false);

    // Signed-out visitors on the retired gate URL get the marketing page.
    expect(
      shouldShowPublicLandingForUnauthenticatedPath("/request-access"),
    ).toBe(true);
  });

  it("keeps the root path as public marketing and logs in to the modern shell", () => {
    expect(getPostLoginRedirectPath()).toBe(MODERN_APP_PATH);
    expect(shouldRenderModernAppShell({ path: "/" })).toBe(false);
  });

  it("keeps explicit modern routes on the modern shell even in classic mode", () => {
    expect(
      shouldRenderModernAppShell({
        path: MODERN_APP_PATH,
        uiMode: "classic",
      }),
    ).toBe(true);
  });

  it("keeps the public root path out of the authenticated app shell", () => {
    expect(
      shouldRenderModernAppShell({
        path: "/",
        uiMode: "classic",
      }),
    ).toBe(false);
  });

  it("retires legacy routes instead of treating them as app-shell entry points", () => {
    expect(isRetiredLegacyPath(LEGACY_APP_PATH)).toBe(true);
    expect(isRetiredLegacyPath("/home")).toBe(true);
    expect(isExplicitLegacyPath(LEGACY_APP_PATH)).toBe(false);
    expect(
      shouldRenderModernAppShell({
        path: LEGACY_APP_PATH,
        uiMode: "modern",
      }),
    ).toBe(false);
  });

  it("does not reuse the legacy /home path as a modern login target", () => {
    expect(
      shouldRenderModernAppShell({
        path: "/home",
        uiMode: "modern",
      }),
    ).toBe(false);
  });

  it("keeps login behind an explicit route so unauthenticated /ax can stay marketing-first", () => {
    expect(isExplicitLoginPath("/login")).toBe(true);
    expect(isExplicitLoginPath("/login/")).toBe(true);
    expect(isExplicitLoginPath(MODERN_APP_PATH)).toBe(false);

    expect(shouldShowPublicLandingForUnauthenticatedPath(MODERN_APP_PATH)).toBe(
      true,
    );
    expect(shouldShowPublicLandingForUnauthenticatedPath("/login")).toBe(false);
  });

  it("treats /admin as a dedicated modern route outside the legacy shell", () => {
    expect(isModernStandalonePath(MODERN_ADMIN_PATH)).toBe(true);
    expect(
      shouldRenderModernAppShell({
        path: MODERN_ADMIN_PATH,
        uiMode: "modern",
      }),
    ).toBe(false);
  });

  it("never connects legacy realtime for public, modern, or stale legacy paths", () => {
    expect(
      shouldConnectLegacyRealtime({
        path: "/",
        uiMode: "modern",
      }),
    ).toBe(false);
    expect(
      shouldConnectLegacyRealtime({
        path: MODERN_APP_PATH,
        uiMode: "classic",
      }),
    ).toBe(false);
    expect(
      shouldConnectLegacyRealtime({
        path: MODERN_ADMIN_PATH,
        uiMode: "modern",
      }),
    ).toBe(false);
    expect(
      shouldConnectLegacyRealtime({
        path: "/podcast",
        uiMode: "modern",
      }),
    ).toBe(false);
    expect(
      shouldConnectLegacyRealtime({
        path: LEGACY_APP_PATH,
        uiMode: "modern",
      }),
    ).toBe(false);
    expect(
      shouldConnectLegacyRealtime({
        path: "/home",
        uiMode: "modern",
      }),
    ).toBe(false);
  });
});
