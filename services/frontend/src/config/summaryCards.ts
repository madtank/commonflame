export function isSummaryCardsFeatureEnabled() {
  return (
    import.meta.env.DEV ||
    (import.meta.env.VITE_ENABLE_SUMMARY_CARDS || "")
      .toString()
      .toLowerCase() === "true"
  );
}

export const SUMMARY_CARDS_FEATURE_ENABLED = isSummaryCardsFeatureEnabled();
