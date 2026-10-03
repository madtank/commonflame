/**
 * Feature flags for gradual rollout of new features
 */

// Check environment variables or use defaults
export const FEATURE_FLAGS = {
  // Enable OpenClaw (external webhook agent) registration
  // Default: true (enabled for all users)
  ENABLE_CLAWDBOT:
    import.meta.env.VITE_ENABLE_CLAWDBOT !== "false" &&
    localStorage.getItem("ff_enable_clawdbot") !== "false",
  // Enable incremental message polling instead of fetching all messages
  ENABLE_INCREMENTAL_POLLING:
    import.meta.env.VITE_ENABLE_INCREMENTAL_POLLING === "true" ||
    localStorage.getItem("ff_incremental_polling") === "true" ||
    false,

  // Enable SSE for real-time messages (Phase 2)
  ENABLE_SSE_MESSAGES:
    import.meta.env.VITE_ENABLE_SSE_MESSAGES === "true" ||
    localStorage.getItem("ff_sse_messages") === "true" ||
    false,

  // Reduce initial message load from 500 to 100
  ENABLE_REDUCED_INITIAL_LOAD:
    import.meta.env.VITE_ENABLE_REDUCED_LOAD === "true" ||
    localStorage.getItem("ff_reduced_load") === "true" ||
    false,
};

// Helper to enable/disable features at runtime (for testing)
export const setFeatureFlag = (
  flag: keyof typeof FEATURE_FLAGS,
  enabled: boolean,
) => {
  const key = `ff_${flag.toLowerCase().replace(/_/g, "_")}`;
  // Set explicit value (not remove) so default-true flags can be disabled
  localStorage.setItem(key, enabled ? "true" : "false");
  // Reload to apply changes
  window.location.reload();
};

// Export for console debugging
if (typeof window !== "undefined") {
  (window as any).featureFlags = FEATURE_FLAGS;
  (window as any).setFeatureFlag = setFeatureFlag;
}
