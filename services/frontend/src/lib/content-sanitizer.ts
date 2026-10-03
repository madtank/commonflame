/**
 * content-sanitizer.ts
 *
 * Strips OpenClaw CLI system info headers and other noise from agent messages
 * before they're rendered in the UI. The AI summary layer handles this gracefully,
 * but raw/condensed views need explicit cleaning.
 *
 * Known patterns:
 * - "🧭 Identity\nChannel: ...\nUser id: ...\nAllowFrom: ..."
 * - "🦞 OpenClaw ..." status blocks
 * - Agent system preamble lines before actual content
 */

// Matches the 🧭 Identity block (multi-line, up to blank line or content start)
const IDENTITY_BLOCK_RE =
  /^🧭\s*Identity\s*\n(?:(?:Channel|User\s*id|AllowFrom|Session|Runtime|Host|Model)[^\n]*\n?)*/gim;

// Matches "🦞 OpenClaw" status blocks (everything until a blank line)
const OPENCLAW_STATUS_RE = /^🦞\s*OpenClaw[^\n]*(?:\n(?![\n\r])[^\n]*)*/gim;

// Matches lines that are purely system metadata (common CLI status lines).
// Supports light markdown wrappers around labels, e.g. `_Runtime:_ ...` or `*Channel:* ...`.
// NOTE: Reasoning/Thinking lines are intentionally excluded here so we don't
// over-strip one-line user-visible payloads like:
//   "Reasoning: I checked all paths and the deploy is complete."
const SYSTEM_LINE_RE =
  /^\s*(?:[*_`~>]+\s*)?(?:Runtime|Channel|Session|Agent|Identity|AllowFrom|User\s*id)(?:\s*[*_`~]+)?\s*:[^\n]*$/gim;

// Narrow stripping for lightweight toggles emitted by some runtimes.
// Supports markdown wrappers too: `_Reasoning:_ on`, `*Thinking*: enabled`.
const REASONING_TOGGLE_LINE_RE =
  /^\s*(?:[*_`~>]+\s*)?(?:Thinking|Reasoning)(?:\s*[*_`~]+)?\s*:\s*(?:[*_`~]+\s*)?(?:on|off|enabled|disabled)\s*$/gim;

/**
 * Remove system info noise from message content.
 * Returns cleaned text with leading/trailing whitespace trimmed.
 */
export function sanitizeMessageContent(raw: string): string {
  if (!raw) return raw;

  let cleaned = raw;

  // Strip identity blocks
  cleaned = cleaned.replace(IDENTITY_BLOCK_RE, "");

  // Strip OpenClaw status blocks
  cleaned = cleaned.replace(OPENCLAW_STATUS_RE, "");

  // Strip standalone system metadata lines
  cleaned = cleaned.replace(SYSTEM_LINE_RE, "");

  // Strip only toggle-style reasoning/thinking lines (keep real one-line content)
  cleaned = cleaned.replace(REASONING_TOGGLE_LINE_RE, "");

  // Collapse 3+ consecutive newlines into 2
  cleaned = cleaned.replace(/\n{3,}/g, "\n\n");

  return cleaned.trim();
}

/**
 * Check if a message appears to be entirely system noise (no real content).
 */
export function isSystemNoiseOnly(raw: string): boolean {
  return sanitizeMessageContent(raw).length === 0;
}

/**
 * Sanitize a URL to prevent XSS via javascript:, data:, vbscript: etc.
 * Only http, https, mailto, hash anchors, and root-relative paths are allowed.
 * Returns "#" for anything that doesn't match the allowlist.
 */
export function sanitizeHref(href: string | undefined | null): string {
  if (!href) return "#";
  const trimmed = href.trim();
  if (/^(https?:\/\/|mailto:|#|\/(?!\/))/i.test(trimmed)) return trimmed;
  return "#";
}

/**
 * Validate a redirect URL against an allowlist for OAuth / post-auth flows.
 *
 * Allowed targets:
 *   - Same-origin paths (/foo, /oauth/callback)
 *   - localhost / 127.0.0.1 on any port (MCP agent local callbacks)
 *   - the current installation origin
 *
 * Returns the URL if safe, or null if it should be blocked.
 * Callers should fall back to a safe default (e.g. "/") on null.
 */
export function sanitizeRedirectUrl(
  url: string | undefined | null,
): string | null {
  if (!url) return null;
  const trimmed = url.trim();

  // Allow same-origin paths (no protocol, starts with /)
  if (/^\/(?!\/)/.test(trimmed)) return trimmed;

  try {
    const parsed = new URL(trimmed);

    // Must be http or https
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }

    const { hostname } = parsed;

    // Allow localhost and loopback (MCP local agent callbacks)
    if (hostname === "localhost" || hostname === "127.0.0.1") {
      return trimmed;
    }

    // Permit redirects to this installation.
    if (typeof window !== "undefined" && parsed.origin === window.location.origin) {
      return trimmed;
    }

    // Block everything else (external domains)
    return null;
  } catch {
    return null;
  }
}
