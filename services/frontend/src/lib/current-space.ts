import type { StoredSpace } from "@/lib/storage";

type PersonalSpaceLike = {
  description?: string | null;
  is_personal?: boolean | null;
};

type CurrentSpaceLike = PersonalSpaceLike & {
  id?: string | null;
  name?: string | null;
  slug?: string | null;
  visibility?: string | null;
  member_count?: number | null;
  is_current?: boolean | null;
};

export type SpaceSwitcherKind = "home" | "private" | "team" | "community";

type SpaceSwitcherSpaceLike = CurrentSpaceLike & {
  name?: string | null;
};

export const normalizeStoredSpaceId = (value?: string | null) => {
  const id = (value || "").trim();
  if (!id || id === "current-space" || id === "default") return null;
  return id;
};

const hasProvisionedHomeDescription = <T extends PersonalSpaceLike>(
  space?: T | null,
) => Boolean(space?.description?.startsWith("Personal workspace for"));

export const isHomeSpace = <T extends PersonalSpaceLike>(space?: T | null) =>
  typeof space?.is_personal === "boolean"
    ? space.is_personal
    : hasProvisionedHomeDescription(space);

export const isProvisionedHomeSpace = <T extends SpaceSwitcherSpaceLike>(
  space?: T | null,
) => {
  const name = (space?.name || "").trim();
  const hasDefaultHomeName = /['\u2019]s\s+workspace$/i.test(name);
  const explicitPersonal =
    typeof space?.is_personal === "boolean" ? space.is_personal : null;

  if (explicitPersonal === false) return false;
  if (explicitPersonal === true) {
    return Boolean(hasProvisionedHomeDescription(space) || hasDefaultHomeName);
  }
  return Boolean(hasProvisionedHomeDescription(space) && hasDefaultHomeName);
};

export const resolveProvisionedHomeSpaceId = <T extends SpaceSwitcherSpaceLike>(
  spaces: T[],
) =>
  spaces.find(
    (space) =>
      normalizeStoredSpaceId(space.id) && isProvisionedHomeSpace(space),
  )?.id || null;

const visibilityIncludes = (visibility: string, values: string[]) =>
  values.some((value) => visibility.includes(value));

export const classifySpaceForSwitcher = <T extends SpaceSwitcherSpaceLike>(
  space?: T | null,
  homeSpaceId?: string | null,
): SpaceSwitcherKind => {
  if (
    space?.id &&
    homeSpaceId &&
    space.id === homeSpaceId &&
    isProvisionedHomeSpace(space)
  ) {
    return "home";
  }

  const visibility = (space?.visibility || "private").toLowerCase();
  if (
    visibilityIncludes(visibility, [
      "public",
      "community",
      "open",
      "subscribable",
    ])
  ) {
    return "community";
  }
  if (
    visibilityIncludes(visibility, ["team", "invite", "shared"]) ||
    (!space?.is_personal && Number(space?.member_count || 0) > 1)
  ) {
    return "team";
  }
  return "private";
};

export const getSpaceSwitcherTypeTag = <T extends SpaceSwitcherSpaceLike>(
  space?: T | null,
  homeSpaceId?: string | null,
) =>
  classifySpaceForSwitcher(
    space,
    homeSpaceId,
  ).toUpperCase() as Uppercase<SpaceSwitcherKind>;

export const getSpaceSwitcherDisplayName = <T extends SpaceSwitcherSpaceLike>(
  space?: T | null,
) => {
  const name = (space?.name || "").trim();
  if (!name) return "Current Space";
  return name.replace(/\s+Workspace$/i, "").trim() || name;
};

export const truncateSpaceSwitcherName = (value: string, maxLength = 28) =>
  value.length > maxLength
    ? `${value.slice(0, maxLength).trimEnd()}...`
    : value;

export const getSpaceSwitcherLabel = <T extends SpaceSwitcherSpaceLike>(
  space?: T | null,
  homeSpaceId?: string | null,
  options?: { maxNameLength?: number },
) => {
  const typeTag = getSpaceSwitcherTypeTag(space, homeSpaceId);
  const displayName = truncateSpaceSwitcherName(
    getSpaceSwitcherDisplayName(space),
    options?.maxNameLength,
  );
  return `${typeTag} ${displayName}`;
};

export const getSpaceSwitcherTitle = <T extends SpaceSwitcherSpaceLike>(
  space?: T | null,
  homeSpaceId?: string | null,
) => {
  const typeTag = getSpaceSwitcherTypeTag(space, homeSpaceId);
  const name = (space?.name || "").trim() || "Current Space";
  const slug = space?.slug ? `@${space.slug}` : null;
  const memberCount =
    typeof space?.member_count === "number" && space.member_count > 0
      ? `${space.member_count} ${
          space.member_count === 1 ? "member" : "members"
        }`
      : null;
  return [typeTag, name, slug, memberCount].filter(Boolean).join(" - ");
};

// General default: trust the backend's is_current flag first.
// Used by ensureCurrentWorkspace (App.tsx) and the shell's space sync.
// Must NOT prefer home over is_current — that causes a switch loop where
// ensureCurrentWorkspace keeps fighting the backend to force home space.
export const selectDefaultSpace = <T extends CurrentSpaceLike>(spaces: T[]) =>
  spaces.find((space) => space.is_current) ||
  spaces.find((space) => hasProvisionedHomeDescription(space)) ||
  spaces.find((space) => Boolean(space.is_personal)) ||
  spaces[0];

const matchesStoredSpace = <T extends CurrentSpaceLike>(
  space: T,
  storedSpaceIdOrSlug?: string | null,
) => {
  if (!storedSpaceIdOrSlug) return false;
  return (
    normalizeStoredSpaceId(space.id) === storedSpaceIdOrSlug ||
    (space.slug || "").trim().toLowerCase() ===
      storedSpaceIdOrSlug.trim().toLowerCase()
  );
};

// Post-login: preserve the backend's sticky current_space_id when it exists.
// If the backend has not marked a current space yet, fall back to the user's
// locally persisted last/home space before broad heuristics like first item.
export const selectLoginSpace = <T extends CurrentSpaceLike>(
  spaces: T[],
  storedSpaceIdOrSlug?: string | null,
) => {
  const currentSpace = spaces.find((space) => space.is_current);
  if (currentSpace) return currentSpace;

  if (storedSpaceIdOrSlug) {
    const storedSpace = spaces.find((space) =>
      matchesStoredSpace(space, storedSpaceIdOrSlug),
    );
    if (storedSpace) return storedSpace;
  }

  return selectDefaultSpace(spaces);
};

export const selectStoredOrCurrentSpace = <T extends CurrentSpaceLike>(
  spaces: T[],
  storedSpaceId?: string | null,
) => {
  const currentSpace = spaces.find((space) => space.is_current);
  if (currentSpace) return currentSpace;

  if (storedSpaceId) {
    const storedSpace = spaces.find((space) => space.id === storedSpaceId);
    if (storedSpace) return storedSpace;
  }

  return selectDefaultSpace(spaces);
};

export const hasResolvedSpace = (
  space?: Pick<StoredSpace, "id" | "name"> | null,
): boolean => Boolean(space?.id && space?.name?.trim());
