/**
 * Centralized type exports
 *
 * Usage:
 *   import type { Agent, Post, Task, Organization } from '@/types';
 *
 * This consolidates duplicate type definitions from across the codebase.
 * When adding new shared types, create a new file in src/types/ and export here.
 *
 * TODO: Migrate remaining components to use these centralized types
 * - Components still using local Agent types: AgentCard, AgentManagement, ChatInput, etc.
 * - Components still using local Post types: MessageList (has extended version)
 * - See docs/REFACTOR_AUDIT.md Section 4 for type safety issues to fix
 */

// Agent types
export type {
  Agent,
  AgentControlState,
  AgentSummary,
  CloudAgent,
  RecentAgentActivity,
  RosterEntryRef,
  Team,
} from "./agent";

// Message types
export type {
  MessageMetadata,
  MessageSummary,
  ParentPostRef,
  Post,
  WaitingMetadata,
} from "./message";

// Organization types
export type {
  Organization,
  OrganizationMember,
  OrganizationSettings,
  StoredOrganization,
} from "./organization";

// Task types
export type {
  Task,
  TaskNote,
  TaskPriority,
  TaskStatus,
  TaskSummary,
} from "./task";

// Admin types
export type { AdminRole, AdminUser, AdminUsersResponse } from "./admin";

// User types
export type {
  UserFeatureFlags,
  UserMetadata,
  UserPlatformFeatures,
  UserProfile,
} from "./user";
