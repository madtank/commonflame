/**
 * Feature flags — server-side flags via /api/v1/flags/ with localStorage fallback.
 *
 * For React components, use useFeatureFlags() hook.
 * For module-level constants, use getFeatureFlag() (reads sessionStorage cache).
 *
 * Admin toggle: PUT /api/v1/flags/{name}?enabled=true&scope=org
 * Local override: localStorage.setItem('ax_flex_layout', 'true')
 */

import { getFeatureFlag } from "@/hooks/useFeatureFlags";

export const USE_FLEX_CHAT_LAYOUT =
  typeof window === "undefined" || getFeatureFlag("ax_flex_layout", true);

// Log active layout on startup
if (typeof window !== "undefined") {
  console.info("ax: chat layout =", USE_FLEX_CHAT_LAYOUT ? "flex" : "fixed");
}
