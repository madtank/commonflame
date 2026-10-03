import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-clean";

// Types based on backend PR #46
export type ArtifactType =
  | "RESEARCH"
  | "CONVERSATION_INSIGHT"
  | "TASK_STATE"
  | "SYSTEM_VALIDATION";

export interface IntelligenceArtifact {
  id: string;
  space_id: string;
  artifact_type: ArtifactType;
  key: string;
  content: string;
  summary_snippet: string; // 200 char auto-generated preview
  version: number;
  access_count: number;
  created_at: string;
  updated_at: string;
  created_by_agent_id?: string;
  created_by_agent_name?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
}

export interface IntelligenceListResponse {
  items: IntelligenceArtifact[];
  total: number;
  limit: number;
  offset: number;
}

export interface IntelligenceSummary {
  id: string;
  space_id: string;
  version: number;
  summary: string;
  key_topics: string[];
  review_type: "incremental" | "full";
  significance_score: number; // 0-100
  artifacts_count: number;
  created_at: string;
}

export interface SummaryHistoryItem {
  id: string;
  version: number;
  review_type: "incremental" | "full";
  significance_score: number;
  trigger_artifact_key?: string;
  key_topics: string[];
  artifacts_count: number;
  created_at: string;
}

export interface SummaryHistoryResponse {
  items: SummaryHistoryItem[];
  total: number;
  has_more: boolean;
}

export interface PromoteRequest {
  key: string;
  artifact_type?: ArtifactType;
  tags?: string[];
}

// Fetch single artifact detail (includes full payload)
export function useIntelligenceDetail(
  spaceId: string | undefined,
  key: string | undefined,
) {
  return useQuery({
    queryKey: ["intelligence-detail", spaceId, key],
    queryFn: async () => {
      if (!spaceId || !key) throw new Error("spaceId and key required");
      const response = await apiClient.get(
        `/api/v1/spaces/${spaceId}/intelligence/${encodeURIComponent(key)}`,
      );
      return response.data;
    },
    enabled: !!spaceId && !!key,
    staleTime: 60000,
  });
}

// Fetch vault artifacts
export function useIntelligenceVault(
  spaceId: string | undefined,
  options?: {
    artifactType?: ArtifactType;
    limit?: number;
    offset?: number;
  },
) {
  return useQuery({
    queryKey: [
      "intelligence",
      spaceId,
      options?.artifactType,
      options?.limit,
      options?.offset,
    ],
    queryFn: async (): Promise<IntelligenceListResponse> => {
      if (!spaceId) throw new Error("spaceId required");
      const params = new URLSearchParams();
      if (options?.artifactType)
        params.set("artifact_type", options.artifactType);
      if (options?.limit) params.set("limit", String(options.limit));
      if (options?.offset) params.set("offset", String(options.offset));

      const response = await apiClient.get(
        `/api/v1/spaces/${spaceId}/intelligence?${params.toString()}`,
      );
      return response.data;
    },
    enabled: !!spaceId,
    staleTime: 30000, // 30 seconds
    refetchInterval: 60000, // Refresh every minute
  });
}

// Fetch AI-generated summary (when backend ready)
export function useIntelligenceSummary(spaceId: string | undefined) {
  return useQuery({
    queryKey: ["intelligence-summary", spaceId],
    queryFn: async (): Promise<IntelligenceSummary> => {
      if (!spaceId) throw new Error("spaceId required");
      const response = await apiClient.get(
        `/api/v1/spaces/${spaceId}/intelligence/summary`,
      );
      return response.data;
    },
    enabled: !!spaceId,
    staleTime: 30000,
    retry: false, // Don't retry if endpoint doesn't exist yet
  });
}

// Fetch summary version history
export function useIntelligenceSummaryHistory(spaceId: string | undefined) {
  return useQuery({
    queryKey: ["intelligence-summary-history", spaceId],
    queryFn: async (): Promise<SummaryHistoryResponse> => {
      if (!spaceId) throw new Error("spaceId required");
      const response = await apiClient.get(
        `/api/v1/spaces/${spaceId}/intelligence/summary/history`,
      );
      return response.data;
    },
    enabled: !!spaceId,
    staleTime: 60000,
    retry: false,
  });
}

// Trigger a fresh AI summary (expensive - use sparingly)
export function useRefreshSummary(spaceId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      if (!spaceId) throw new Error("spaceId required");
      const response = await apiClient.post(
        `/api/v1/spaces/${spaceId}/intelligence/summary/refresh`,
      );
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["intelligence-summary", spaceId],
      });
      queryClient.invalidateQueries({
        queryKey: ["intelligence-summary-history", spaceId],
      });
    },
  });
}

// Promote ephemeral context to vault
export function usePromoteToVault(spaceId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (request: PromoteRequest) => {
      if (!spaceId) throw new Error("spaceId required");
      const response = await apiClient.post(
        `/api/v1/spaces/${spaceId}/intelligence/promote`,
        request,
      );
      return response.data;
    },
    onSuccess: () => {
      // Invalidate vault query to refetch
      queryClient.invalidateQueries({ queryKey: ["intelligence", spaceId] });
      // Also invalidate summary as it may have been updated
      queryClient.invalidateQueries({
        queryKey: ["intelligence-summary", spaceId],
      });
    },
  });
}

// Delete artifact from vault
export function useDeleteArtifact(spaceId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (artifactId: string) => {
      if (!spaceId) throw new Error("spaceId required");
      const response = await apiClient.delete(
        `/api/v1/spaces/${spaceId}/intelligence/${artifactId}`,
      );
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["intelligence", spaceId] });
      queryClient.invalidateQueries({
        queryKey: ["intelligence-summary", spaceId],
      });
    },
  });
}
