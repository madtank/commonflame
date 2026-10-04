/**
 * Display utility functions.
 *
 * Spec: WIDGET-002 §5.1 — Universal Display Rules
 * - "Hide or transform: backend enum names"
 * - "Hide or transform: UUIDs"
 */

/** Known abbreviations that should stay uppercased. */
const ABBREVIATIONS = new Set(["cli", "mcp", "api", "sso", "gcp"]);

const TITLE_CASE_STOP_WORDS = new Set(["all"]);

/** Enum values with special human-readable mappings. */
const AGENT_TYPE_MAP: Record<string, string> = {
  cloud_gcp: "Cloud",
  webhook: "OpenClaw",
  user: "Human",
};

/**
 * Convert a backend agent_type enum (e.g. "EXTERNAL_GATEWAY", "space_agent")
 * into a human-readable label (e.g. "External Gateway", "Space Agent").
 */
export function humanizeAgentType(raw: string | null | undefined): string {
  if (!raw || raw.trim() === "") return "Agent";

  const lower = raw.toLowerCase();

  // Check explicit mappings first
  if (AGENT_TYPE_MAP[lower]) return AGENT_TYPE_MAP[lower];

  // Known abbreviations render as uppercase
  if (ABBREVIATIONS.has(lower)) return raw.toUpperCase();

  // Split on underscores, title-case each word
  return lower
    .split("_")
    .map((word) =>
      ABBREVIATIONS.has(word)
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(" ");
}

/**
 * Convert an agent handle (e.g. "wire_tap", "logic_runner_677") into a
 * human-readable display name (e.g. "Wire Tap", "Logic Runner 677").
 * Preserves known abbreviations (MCP, CLI, etc.) and special cases like "Commonflame".
 */
export function humanizeHandle(raw: string | null | undefined): string {
  if (!raw || raw.trim() === "") return "Agent";
  const cleaned = raw.replace(/^@/, "").trim();
  if (cleaned.toLowerCase() === "ax") return "Commonflame";
  return cleaned
    .toLowerCase()
    .split("_")
    .map((word) =>
      ABBREVIATIONS.has(word)
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(" ");
}

/**
 * Format a space label for display. Shows the workspace name when available,
 * falls back to a truncated UUID prefix. Never shows a raw full UUID.
 */
export function formatSpaceLabel(
  name: string | null | undefined,
  spaceId: string | null | undefined,
): string {
  if (name && name.trim() !== "") return name;
  if (spaceId && spaceId.trim() !== "") {
    // Show first segment of UUID (8 chars) with ellipsis
    return spaceId.split("-")[0] + "…";
  }
  return "";
}
