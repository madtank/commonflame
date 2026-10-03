/**
 * Centralized User type definitions
 * Import from '@/types' instead of defining locally
 */

/** User feature flags for gradual rollout of new features */
export interface UserFeatureFlags {
  /** Enable v2 agent engine for all interactions (Sovereign Toggle) */
  v2_enabled?: boolean;
  /** Allow access to beta/experimental features in UI */
  can_access_beta_features?: boolean;
}

/** Subscription tier levels */
export type SubscriptionTier = "visitor" | "plus" | "pro";

/** Platform features enabled for the user */
export interface UserPlatformFeatures {
  /** User can create cloud agents */
  cloud_agent_creation_enabled?: boolean;
  /** User can use V2 engine (requires plus/pro tier) */
  v2_engine_enabled?: boolean;
  /** User's subscription tier */
  subscription_tier?: SubscriptionTier;
}

/** User metadata stored in localStorage after login */
export interface UserMetadata {
  id?: string;
  username?: string;
  handle?: string;
  email?: string;
  avatar_url?: string | null;
  admin?: boolean;
  is_admin?: boolean;
  role?: string;
  platform_features?: UserPlatformFeatures;
  feature_flags?: UserFeatureFlags;
}

/** User profile from API */
export interface UserProfile {
  id: string;
  username: string;
  email?: string;
  avatar_url?: string | null;
  admin?: boolean;
  is_admin?: boolean;
  platform_features?: UserPlatformFeatures;
  feature_flags?: UserFeatureFlags;
}
