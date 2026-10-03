/**
 * org→space migration helpers
 *
 * During the migration from org_id to space_id, API responses return both fields.
 * These helpers ensure the frontend always prefers space_id, falling back to org_id.
 *
 * Once the backend drops org_id (Phase 3), these become trivial passthrough
 * and can be removed during cleanup.
 */

/** Extract the canonical space ID from any object that may have space_id and/or org_id */
export function getSpaceId(
  obj: { space_id?: string; org_id?: string } | null | undefined,
): string | undefined {
  return obj?.space_id ?? obj?.org_id;
}

/** Normalize an API response object to always have space_id populated */
export function normalizeSpaceId<
  T extends { space_id?: string; org_id?: string },
>(obj: T): T & { space_id?: string } {
  if (obj.space_id) return obj;
  if (obj.org_id) return { ...obj, space_id: obj.org_id };
  return obj;
}
