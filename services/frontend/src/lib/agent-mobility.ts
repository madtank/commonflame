// Centralized frontend helpers for Agent Mobility Modes
// Keep FOLLOW_UUID in sync with backend constants

export const FOLLOW_UUID = '11111111-1111-1111-1111-111111111111' as const

export type MobilityMode = 'free' | 'follow' | 'pinned'

export function normalizeUUID(val: unknown): string | null {
  if (!val) return null
  try {
    // Some backends return UUID objects; coerce to string when possible
    const str = (val as any)?.toString ? (val as any).toString() : String(val)
    return str || null
  } catch {
    return null
  }
}

// Determine mobility mode from an agent-like object with possible fields:
// org_id, pinned_to_org, pinned_org_id, settings.follow_user, follow_user
export function getMobilityMode(agentLike: any): MobilityMode {
  const orgId = normalizeUUID(agentLike?.org_id)
  const pinnedRaw = agentLike?.pinned_to_org ?? agentLike?.pinned_org_id ?? null
  const pinned = normalizeUUID(pinnedRaw)

  // Follow if either field equals FOLLOW_UUID, or explicit follow flag present
  if (pinned === FOLLOW_UUID || orgId === FOLLOW_UUID) return 'follow'
  if (agentLike?.follow_user === true || agentLike?.settings?.follow_user === true) return 'follow'

  // Pinned if pinned_to_org present with a real org id
  if (pinned) return 'pinned'

  return 'free'
}

export function isFollowing(agentLike: any): boolean {
  return getMobilityMode(agentLike) === 'follow'
}

export function isPinned(agentLike: any): boolean {
  return getMobilityMode(agentLike) === 'pinned'
}

export function getMobilityDisplay(mode: MobilityMode) {
  switch (mode) {
    case 'follow':
      return { icon: '🧭', label: 'Following', tooltip: 'This agent follows your workspace.', variant: 'default' as const }
    case 'pinned':
      return { icon: '🔒', label: 'Pinned', tooltip: 'This agent is locked to a specific workspace.', variant: 'secondary' as const }
    default:
      return { icon: '🚀', label: 'Free Roam', tooltip: 'This agent can move freely between workspaces.', variant: 'outline' as const }
  }
}

// Convenience to build a user-facing workspace label for the card chip
export function buildWorkspaceDisplay(agentLike: any): { primary: string; resolved?: string } {
  const mode = getMobilityMode(agentLike)
  const currentSpaceName: string | undefined = agentLike?.current_space_name
  const currentSpaceSlug: string | undefined = agentLike?.current_space_slug
  const currentOrgId: string | null = normalizeUUID(agentLike?.current_org_id)

  const fallback = currentSpaceName || (currentSpaceSlug ? `@${currentSpaceSlug}` : (currentOrgId ? `${currentOrgId.slice(0, 6)}…` : 'No space'))

  if (mode === 'follow') {
    // Show it mirrors user's workspace and optionally the resolved name
    return { primary: 'Follows your workspace', resolved: fallback }
  }
  // Pinned and Free show the current workspace or pinned workspace name passed in via current_* props
  return { primary: fallback }
}
