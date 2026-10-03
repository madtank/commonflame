/**
 * Cool Agent Name Generator
 *
 * Generates creative, tech-inspired agent names for the Waystation platform
 */

const PREFIXES = [
  "code",
  "data",
  "cyber",
  "neo",
  "hyper",
  "meta",
  "proto",
  "synth",
  "pixel",
  "byte",
  "logic",
  "apex",
  "core",
  "prime",
  "alpha",
  "beta",
  "delta",
  "omega",
  "ghost",
  "spark",
  "flash",
  "nova",
  "lunar",
  "solar",
  "zen",
  "swift",
  "turbo",
  "ultra",
  "void",
  "prism",
  "flux",
  "arc",
  "echo",
  "hex",
  "ion",
];

const MIDDLES = [
  "forge",
  "craft",
  "smith",
  "pilot",
  "scout",
  "guard",
  "sage",
  "mind",
  "core",
  "pulse",
  "wave",
  "flow",
  "storm",
  "ray",
  "bot",
  "sync",
  "link",
  "node",
  "keeper",
  "seeker",
  "runner",
  "maker",
];

const SUFFIXES = [
  "ai",
  "x",
  "z",
  "pro",
  "neo",
  "one",
  "hub",
  "net",
  "gen",
  "v2",
  "mk2",
  "ace",
  "max",
];

const STANDALONE_NAMES = [
  "Atlas",
  "Phoenix",
  "Oracle",
  "Nexus",
  "Cipher",
  "Vertex",
  "Vortex",
  "Helix",
  "Cortex",
  "Sphinx",
  "Zenith",
  "Nebula",
  "Titan",
  "Apollo",
  "Orion",
  "Vega",
  "Rigel",
  "Lyra",
  "Draco",
];

const ADJECTIVES = [
  "swift",
  "wise",
  "bright",
  "sharp",
  "quick",
  "agile",
  "sleek",
  "silent",
  "cosmic",
  "quantum",
  "neural",
];

export type NameStyle = "compound" | "standalone" | "adjective_noun" | "random";

export interface GeneratorOptions {
  style?: NameStyle;
  separator?: string;
  lowercase?: boolean;
  includeNumber?: boolean;
}

/**
 * Generate a random agent name
 *
 * @param options Configuration options for name generation
 * @returns A randomly generated agent name
 */
export function generateAgentName(options: GeneratorOptions = {}): string {
  const {
    style = "random",
    separator = "_",
    lowercase = true,
    includeNumber = true, // Default to true for better uniqueness
  } = options;

  let name: string;
  let actualStyle = style;

  // If random, pick a random style
  if (style === "random") {
    const styles: NameStyle[] = ["compound", "standalone", "adjective_noun"];
    actualStyle = styles[Math.floor(Math.random() * styles.length)];
  }

  switch (actualStyle) {
    case "compound":
      name = generateCompoundName(separator);
      break;
    case "standalone":
      name = generateStandaloneName();
      break;
    case "adjective_noun":
      name = generateAdjectiveNounName(separator);
      break;
    default:
      name = generateCompoundName(separator);
  }

  // Add random 3-digit number suffix by default for uniqueness
  if (includeNumber) {
    const num = Math.floor(Math.random() * 900) + 100; // 100-999
    name = `${name}${separator}${num}`;
  }

  // Ensure name doesn't exceed 32 characters (frontend UI constraint)
  if (name.length > 32) {
    // Truncate the base name and keep the number
    const numSuffix = name.substring(name.lastIndexOf(separator));
    const baseName = name.substring(0, name.lastIndexOf(separator));
    const maxBaseLength = 32 - numSuffix.length;
    name = baseName.substring(0, maxBaseLength) + numSuffix;
  }

  return lowercase ? name.toLowerCase() : name;
}

/**
 * Generate a compound name like "code_forge" or "neo_pilot"
 * Keeps it to 2 parts max for brevity
 */
function generateCompoundName(separator: string): string {
  const prefix = PREFIXES[Math.floor(Math.random() * PREFIXES.length)];
  const middle = MIDDLES[Math.floor(Math.random() * MIDDLES.length)];

  return `${prefix}${separator}${middle}`;
}

/**
 * Generate a standalone cool name like "Phoenix" or "Atlas"
 */
function generateStandaloneName(): string {
  const base =
    STANDALONE_NAMES[Math.floor(Math.random() * STANDALONE_NAMES.length)];

  // Sometimes add a suffix
  if (Math.random() > 0.7) {
    const suffix = SUFFIXES[Math.floor(Math.random() * SUFFIXES.length)];
    return `${base}${suffix}`;
  }

  return base;
}

/**
 * Generate adjective + noun name like "swift_oracle"
 */
function generateAdjectiveNounName(separator: string): string {
  const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
  const noun =
    STANDALONE_NAMES[Math.floor(Math.random() * STANDALONE_NAMES.length)];
  return `${adj}${separator}${noun}`;
}

/**
 * Generate multiple name suggestions
 *
 * @param count Number of suggestions to generate
 * @param options Generator options
 * @returns Array of unique name suggestions
 */
export function generateNameSuggestions(
  count: number = 5,
  options: GeneratorOptions = {},
): string[] {
  const suggestions = new Set<string>();
  let attempts = 0;
  const maxAttempts = count * 10; // Prevent infinite loop

  while (suggestions.size < count && attempts < maxAttempts) {
    suggestions.add(generateAgentName(options));
    attempts++;
  }

  return Array.from(suggestions);
}

// Reserved agent names (must match backend)
const RESERVED_AGENT_NAMES = new Set([
  "admin",
  "system",
  "root",
  "api",
  "mcp",
  "platform",
  "service",
  "agent",
  "bot",
  "anonymous",
  "unknown",
  "default",
]);

// Cloud agent names that can be shared across users (must match backend)
// These agents are created automatically for each user during onboarding
const CLOUD_AGENT_NAMES = new Set([
  "ax_guide", // Onboarding assistant
]);

/**
 * Validate agent name (frontend UI validation)
 * Pattern: ^[a-zA-Z][a-zA-Z0-9_-]*$
 * - Must start with a LETTER (not number)
 * - Can contain: letters, numbers, underscore, hyphen
 * - Length: 3-32 characters (frontend constraint for better UX)
 * - Cannot be a reserved name
 */
export function validateAgentName(name: string): {
  isValid: boolean;
  error: string;
} {
  if (!name) {
    return { isValid: false, error: "Agent name is required" };
  }

  // Check length
  if (name.length < 3) {
    return {
      isValid: false,
      error: "Agent name must be at least 3 characters",
    };
  }

  if (name.length > 32) {
    return { isValid: false, error: "Agent name cannot exceed 32 characters" };
  }

  // Check if reserved
  if (RESERVED_AGENT_NAMES.has(name.toLowerCase())) {
    return {
      isValid: false,
      error: `'${name}' is a reserved name and cannot be used`,
    };
  }

  // Check pattern: must start with letter, contain only letters, numbers, underscore, hyphen
  if (!/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(name)) {
    if (!/^[a-zA-Z]/.test(name)) {
      return { isValid: false, error: "Agent name must start with a letter" };
    }
    return {
      isValid: false,
      error:
        "Agent name can only contain letters, numbers, underscore, and hyphen",
    };
  }

  return { isValid: true, error: "" };
}

/**
 * Check if a name is available (basic validation)
 * This is a client-side check - server-side validation is still required
 *
 * @deprecated Use validateAgentName instead for detailed error messages
 */
export function isValidAgentName(name: string): boolean {
  return validateAgentName(name).isValid;
}

/** Maximum agent name length - used for UI validation */
export const AGENT_NAME_MAX_LENGTH = 32;

/**
 * Sanitize an agent name by removing invalid characters
 * Only allows: letters, numbers, underscore, hyphen
 * Forces lowercase for consistency
 */
export function sanitizeAgentName(name: string): string {
  if (!name) return "";
  // Remove any characters that aren't alphanumeric, underscore, or hyphen
  // Force lowercase for consistency
  return name
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "")
    .slice(0, AGENT_NAME_MAX_LENGTH);
}

/**
 * Validate agent name input and return an error message or null if valid
 * @param name - The name to validate
 * @param existingNames - Set of existing agent names to check for duplicates
 * @returns Error message string or null if valid
 */
export function validateAgentNameInput(
  name: string,
  existingNames?: Set<string>,
): string | null {
  const result = validateAgentName(name);
  if (!result.isValid) {
    return result.error;
  }

  // Check for duplicates if existing names provided
  if (existingNames && existingNames.has(name.toLowerCase())) {
    return "This agent name is already taken";
  }

  return null;
}

/**
 * Generate a random agent name that doesn't conflict with existing names
 * @param existingNames - Set of existing agent names to avoid
 * @param maxAttempts - Maximum attempts before giving up
 * @returns A unique agent name
 */
export function generateRandomAgentName(
  existingNames?: Set<string>,
  maxAttempts: number = 50,
): string {
  for (let i = 0; i < maxAttempts; i++) {
    const name = generateAgentName({
      style: "random",
      lowercase: true,
      includeNumber: true,
    });
    if (!existingNames || !existingNames.has(name.toLowerCase())) {
      return name;
    }
  }
  // Fallback: add timestamp for uniqueness
  return `agent_${Date.now() % 100000}`;
}
