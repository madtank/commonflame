import { api } from "../lib/api-clean";

// Local cache to prevent redundant calls
const summaryCache = new Map<string, string>();

// In-flight requests to deduplicate concurrent calls for same ID
const pendingRequests = new Map<string, Promise<string>>();

// Track failed attempts for exponential backoff
const failureCount = new Map<string, number>();
const lastFailureTime = new Map<string, number>();

// Configuration
const MAX_RETRY_ATTEMPTS = 2;
const BASE_RETRY_DELAY = 1000; // 1 second
const MAX_RETRY_DELAY = 10000; // 10 seconds

/**
 * Generate a smart fallback summary by intelligently truncating the message
 */
function createFallbackSummary(content: string): string {
  if (!content || content.length <= 100) return content;

  // Try to truncate at sentence boundaries
  const sentences = content.split(/[.!?]+/);
  if (
    sentences.length > 1 &&
    sentences[0].length > 30 &&
    sentences[0].length <= 150
  ) {
    return sentences[0].trim() + "...";
  }

  // Fall back to word boundaries
  const words = content.split(/\s+/);
  if (words.length > 15) {
    return words.slice(0, 15).join(" ") + "...";
  }

  // Last resort: character truncation
  return content.substring(0, 120) + "...";
}

/**
 * Calculate delay for exponential backoff
 */
function getRetryDelay(attempt: number): number {
  const delay = Math.min(
    BASE_RETRY_DELAY * Math.pow(2, attempt),
    MAX_RETRY_DELAY,
  );
  return delay + Math.random() * 1000; // Add jitter
}

export const SummarizerService = {
  /**
   * Generates a concise summary for a message using the backend AI summarization endpoint.
   * Includes retry logic and fallback to smart text truncation.
   * @param messageId - Unique ID of the message to summarize
   * @param originalContent - Original message content for fallback (optional)
   * @returns Promise<string> - The generated summary or fallback
   */
  async getSummary(
    messageId: string | number,
    originalContent?: string,
  ): Promise<string> {
    const cacheKey = String(messageId);

    // 1. Check Cache
    if (summaryCache.has(cacheKey)) {
      return summaryCache.get(cacheKey)!;
    }

    // 2. Check Pending Requests (Deduplication)
    if (pendingRequests.has(cacheKey)) {
      return pendingRequests.get(cacheKey)!;
    }

    // 3. Initiate Request with retry logic
    const requestPromise = (async () => {
      const failures = failureCount.get(cacheKey) || 0;

      // Check if we should delay due to recent failures
      const lastFailed = lastFailureTime.get(cacheKey);
      if (lastFailed && failures > 0) {
        const timeSinceFailure = Date.now() - lastFailed;
        const requiredDelay = getRetryDelay(failures - 1);
        if (timeSinceFailure < requiredDelay) {
          // Too soon to retry, provide fallback
          if (originalContent) {
            const fallback = createFallbackSummary(originalContent);
            summaryCache.set(cacheKey, fallback);
            return fallback;
          }
          return "⏳ Summary will retry shortly...";
        }
      }

      let lastError: any;
      for (let attempt = 0; attempt < MAX_RETRY_ATTEMPTS; attempt++) {
        try {
          // Add delay between retry attempts
          if (attempt > 0) {
            await new Promise((resolve) =>
              setTimeout(resolve, getRetryDelay(attempt - 1)),
            );
          }

          // Call the backend endpoint
          const response = await api.summarizeMessage(cacheKey);
          const summaryText = response.summary || "Could not generate summary.";

          // Success! Clear failure tracking
          failureCount.delete(cacheKey);
          lastFailureTime.delete(cacheKey);

          // Update Cache
          summaryCache.set(cacheKey, summaryText);
          return summaryText;
        } catch (error: any) {
          lastError = error;
          console.warn(
            `Summary attempt ${attempt + 1} failed for ${messageId}:`,
            error?.message,
          );

          // Don't retry on certain errors
          if (
            error?.response?.status === 404 ||
            error?.response?.status === 403
          ) {
            break;
          }
        }
      }

      // All retries failed, track failure and provide helpful response
      failureCount.set(cacheKey, failures + 1);
      lastFailureTime.set(cacheKey, Date.now());

      console.error("SummarizerService failed after retries:", lastError);

      // Provide intelligent fallback based on error type and available content
      if (originalContent) {
        const fallback = createFallbackSummary(originalContent);
        summaryCache.set(cacheKey, fallback);
        return fallback;
      }

      // Error-specific messaging
      if (lastError?.response?.status === 500) {
        return "🤖 AI summarizer temporarily unavailable";
      } else if (lastError?.response?.status === 429) {
        return "⏱️ Rate limited - summary will retry automatically";
      } else if (lastError?.response?.status === 404) {
        return "❌ Message not found";
      } else if (lastError?.response?.status === 403) {
        return "🔒 Unauthorized to summarize this message";
      } else if (!navigator.onLine) {
        return "📶 Offline - summary will load when connected";
      }

      return "⚠️ Summary temporarily unavailable";
    })();

    pendingRequests.set(cacheKey, requestPromise);

    try {
      return await requestPromise;
    } finally {
      pendingRequests.delete(cacheKey);
    }
  },

  /**
   * Clear the cache if needed (e.g. on language change or forcing refresh)
   */
  clearCache() {
    summaryCache.clear();
    failureCount.clear();
    lastFailureTime.clear();
  },

  /**
   * Reset failure tracking for a specific message (useful for manual retry)
   */
  resetFailures(messageId: string | number) {
    const cacheKey = String(messageId);
    failureCount.delete(cacheKey);
    lastFailureTime.delete(cacheKey);
    summaryCache.delete(cacheKey);
  },

  /**
   * Get failure stats for debugging
   */
  getFailureStats() {
    return {
      totalFailures: Array.from(failureCount.values()).reduce(
        (sum, count) => sum + count,
        0,
      ),
      failedMessages: failureCount.size,
      cacheSize: summaryCache.size,
    };
  },
};
