/**
 * Agent Control Service
 *
 * API client for managing agent kill switches and rate limits
 */

import { apiClient } from "@/lib/api-clean";
import { storage } from "@/lib/storage";

export interface AgentControlState {
  is_disabled: boolean;
  disabled_reason?: string;
  disabled_by: string[];
  disabled_until?: string | null;
  no_reply?: boolean;
  no_reply_reason?: string;
  no_reply_by?: string[];
  no_reply_until?: string | null;
  routing_only?: boolean;
  routing_only_reason?: string;
  routing_only_by?: string[];
  routing_only_until?: string | null;
  user_hourly_limit?: number;
  user_daily_limit?: number;
  agent_hourly_limit?: number;
  agent_daily_limit?: number;
}

export interface AgentControlUpdate {
  scope: "agent" | "workspace" | "global" | "managed";
  disabled?: boolean;
  disabled_until?: string | null;
  reason?: string | null;
  no_reply?: boolean;
  no_reply_reason?: string | null;
  no_reply_until?: string | null;
  routing_only?: boolean;
  routing_only_reason?: string | null;
  routing_only_until?: string | null;
  user_hourly_limit?: number;
  user_daily_limit?: number;
  agent_hourly_limit?: number;
  agent_daily_limit?: number;
  target_slug?: string;
}

export const agentControlService = {
  requestConfig() {
    const spaceId = storage.getCurrentSpaceId?.();
    if (!spaceId) return undefined;
    return {
      headers: { "X-Space-Id": spaceId },
      params: { space_id: spaceId },
    };
  },

  /**
   * Get current control state for an agent
   */
  async getControlState(agentId: string): Promise<AgentControlState> {
    const response = await apiClient.get(
      `/auth/agents/${agentId}/control`,
      this.requestConfig(),
    );
    return response.data;
  },

  /**
   * Update agent control state (kill switch, rate limits)
   */
  async updateControl(
    agentId: string,
    update: AgentControlUpdate,
  ): Promise<AgentControlState> {
    const response = await apiClient.patch(
      `/auth/agents/${agentId}/control`,
      update,
      this.requestConfig(),
    );
    return response.data;
  },

  /**
   * Quick disable an agent (user-level)
   */
  async disableAgent(
    agentId: string,
    reason: string = "Disabled by user",
  ): Promise<AgentControlState> {
    return this.updateControl(agentId, {
      scope: "agent",
      disabled: true,
      disabled_until: null,
      reason,
    });
  },

  /**
   * Temporarily disable an agent for a fixed duration.
   */
  async breakAgent(
    agentId: string,
    durationSeconds: number,
    reason: string = "Temporarily disabled by owner",
  ): Promise<AgentControlState> {
    const disabledUntil = new Date(
      Date.now() + durationSeconds * 1000,
    ).toISOString();
    return this.updateControl(agentId, {
      scope: "agent",
      disabled: true,
      disabled_until: disabledUntil,
      reason,
    });
  },

  /**
   * Quick enable an agent (user-level)
   */
  async enableAgent(agentId: string): Promise<AgentControlState> {
    return this.updateControl(agentId, {
      scope: "agent",
      disabled: false,
      disabled_until: null,
      reason: null,
      no_reply: false,
      no_reply_reason: null,
      no_reply_until: null,
      routing_only: false,
      routing_only_reason: null,
      routing_only_until: null,
    });
  },

  /**
   * Update rate limits for an agent
   */
  async updateRateLimits(
    agentId: string,
    limits: {
      user_hourly_limit?: number;
      user_daily_limit?: number;
      agent_hourly_limit?: number;
      agent_daily_limit?: number;
    },
  ): Promise<AgentControlState> {
    return this.updateControl(agentId, {
      scope: "agent",
      ...limits,
    });
  },
};
