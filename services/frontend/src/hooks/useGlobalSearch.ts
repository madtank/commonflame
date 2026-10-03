import { useCallback, useState } from "react";
import { api } from "@/lib/api-clean";

export type GlobalSearchScope = "all" | "messages" | "tasks" | "agents";
export type GlobalResultType = "message" | "task" | "agent";

export interface GlobalSearchResult {
  id: string;
  type: GlobalResultType;
  score?: number;
  title?: string;
  content?: string;
  summary?: string;
  snippet?: string;
  highlight?: string;
  author?: string;
  channel?: string;
  timestamp?: string;
  status?: string;
  priority?: string;
  assigned_to?: string | null;
  name?: string;
  handle?: string;
  description?: string;
  specialization?: string;
  capabilities?: string[];
  spaceId?: string;
  spaceName?: string;
  spaceIcon?: string;
  spaceSlug?: string;
}

export interface GlobalSearchResponse {
  results: GlobalSearchResult[];
  total: number;
  trends: string[];
  query: string;
  fallback: boolean;
  summary?: string | null;
}

interface GlobalSearchParams {
  query: string;
  scope?: GlobalSearchScope;
  limit?: number;
  offset?: number;
  spaceId?: string | null;
}

const toNumberScore = (value: unknown): number | undefined => {
  if (value === null || value === undefined) return undefined;
  const n = typeof value === "number" ? value : parseFloat(String(value));
  if (!Number.isFinite(n)) return undefined;
  if (n > 1 && n <= 100) return n / 100;
  return n;
};

const normalizeType = (value: unknown): GlobalResultType => {
  const type = String(value || "").toLowerCase();
  if (type.includes("task")) return "task";
  if (type.includes("agent")) return "agent";
  return "message";
};

const extractSnippet = (raw: Record<string, unknown>) => {
  const candidate =
    raw.snippet ??
    raw.highlight ??
    raw.fragment ??
    raw.excerpt ??
    raw.preview ??
    raw.summary;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate.trim();
  }
  return undefined;
};

const extractSpaceInfo = (
  raw: Record<string, unknown>,
): {
  spaceId: string | undefined;
  spaceName: string | undefined;
  spaceIcon: string | undefined;
  spaceSlug: string | undefined;
} => {
  const spaceIdRaw =
    raw.space_id ??
    raw.organization_id ??
    raw.org_id ??
    raw.current_org_id ??
    raw.space ??
    raw.spaceId ??
    undefined;
  const spaceNameRaw =
    raw.space_name ??
    raw.organization_name ??
    raw.org_name ??
    raw.space ??
    undefined;
  const spaceIconRaw = raw.space_icon_url ?? raw.organization_icon ?? undefined;
  const spaceSlugRaw = raw.space_slug ?? raw.organization_slug ?? undefined;

  return {
    spaceId:
      spaceIdRaw !== undefined && spaceIdRaw !== null
        ? String(spaceIdRaw)
        : undefined,
    spaceName:
      spaceNameRaw !== undefined && spaceNameRaw !== null
        ? String(spaceNameRaw)
        : undefined,
    spaceIcon:
      spaceIconRaw !== undefined && spaceIconRaw !== null
        ? String(spaceIconRaw)
        : undefined,
    spaceSlug:
      spaceSlugRaw !== undefined && spaceSlugRaw !== null
        ? String(spaceSlugRaw)
        : undefined,
  };
};
const normalizeResult = (raw: Record<string, unknown>): GlobalSearchResult => {
  const type = normalizeType(
    raw.type ?? raw.entity_type ?? raw.kind ?? raw.scope,
  );
  const id = String(
    raw.id ?? raw.task_id ?? raw.message_id ?? raw.agent_id ?? raw.uuid ?? "",
  );
  const score = toNumberScore(
    raw.score ??
      raw.relevance ??
      raw.similarity ??
      raw.relevance_score ??
      raw.semantic_score ??
      raw.confidence ??
      raw.score_pct ??
      raw.score_percent ??
      raw.scorePercent,
  );
  const snippet = extractSnippet(raw);
  const space = extractSpaceInfo(raw);
  const handleCandidate =
    raw.handle ??
    raw.agent_handle ??
    raw.username ??
    raw.agent_name ??
    (typeof raw.name === "string" ? raw.name : undefined);
  const normalizedHandle =
    typeof handleCandidate === "string"
      ? handleCandidate.startsWith("@")
        ? handleCandidate
        : `@${handleCandidate}`
      : undefined;

  if (type === "task") {
    return {
      id,
      type,
      score,
      snippet,
      title: String(raw.title ?? raw.name ?? "Untitled task"),
      summary: typeof raw.summary === "string" ? raw.summary : undefined,
      content:
        typeof raw.description === "string" ? raw.description : undefined,
      status: raw.status ? String(raw.status) : undefined,
      priority: raw.priority ? String(raw.priority) : undefined,
      assigned_to:
        raw.assigned_to !== undefined && raw.assigned_to !== null
          ? String(raw.assigned_to)
          : null,
      timestamp: String(raw.updated_at ?? raw.created_at ?? ""),
      spaceId: space.spaceId,
      spaceName: space.spaceName,
      spaceIcon: space.spaceIcon,
      spaceSlug: space.spaceSlug,
    };
  }

  if (type === "agent") {
    return {
      id,
      type,
      score,
      snippet,
      name: String(raw.name ?? raw.agent_name ?? "Unnamed agent"),
      description:
        typeof raw.description === "string" ? raw.description : undefined,
      specialization:
        typeof raw.specialization === "string" ? raw.specialization : undefined,
      capabilities: Array.isArray(raw.capabilities)
        ? raw.capabilities.map((cap) => String(cap))
        : undefined,
      status: raw.status ? String(raw.status) : undefined,
      handle: normalizedHandle,
      spaceId: space.spaceId,
      spaceName: space.spaceName,
      spaceIcon: space.spaceIcon,
      spaceSlug: space.spaceSlug,
    };
  }

  return {
    id,
    type,
    score,
    snippet,
    content: String(raw.content ?? raw.text ?? raw.body ?? ""),
    summary: typeof raw.summary === "string" ? raw.summary : undefined,
    author: raw.author
      ? String(raw.author)
      : raw.sender
        ? String(raw.sender)
        : undefined,
    channel: raw.channel ? String(raw.channel) : undefined,
    timestamp: String(raw.timestamp ?? raw.created_at ?? raw.uploaded_at ?? ""),
    spaceId: space.spaceId,
    spaceName: space.spaceName,
    spaceIcon: space.spaceIcon,
    spaceSlug: space.spaceSlug,
  };
};

const dedupeResults = (items: GlobalSearchResult[]) => {
  const seen = new Map<string, GlobalSearchResult>();
  for (const item of items) {
    const key = `${item.type}:${item.id}`;
    if (!seen.has(key)) {
      seen.set(key, item);
      continue;
    }
    const existing = seen.get(key);
    if (!existing) continue;
    const existingScore = existing.score ?? -1;
    const nextScore = item.score ?? -1;
    if (nextScore > existingScore) {
      seen.set(key, item);
    }
  }
  return Array.from(seen.values());
};

export function useGlobalSearch() {
  const [results, setResults] = useState<GlobalSearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [trends, setTrends] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fallback, setFallback] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);

  const search = useCallback(async (params: GlobalSearchParams) => {
    const query = params.query?.trim() ? params.query.trim() : "*";
    const scope = params.scope ?? "all";
    const limit = params.limit ?? 20;
    const offset = params.offset ?? 0;

    setLoading(true);
    setError(null);
    setFallback(false);

    try {
      const response = await api.searchGlobal({
        query,
        scope,
        limit,
        offset,
        spaceId: params.spaceId ?? null,
      });

      const rawResults = Array.isArray(response?.results)
        ? response.results
        : Array.isArray(response?.data?.results)
          ? response.data.results
          : [];
      const normalized = dedupeResults(
        rawResults.map((item: any) => normalizeResult(item)),
      );
      const responseSummary =
        typeof response?.summary === "string"
          ? response.summary
          : typeof response?.data?.summary === "string"
            ? response.data.summary
            : null;
      const normalizedSummary =
        typeof responseSummary === "string" && responseSummary.trim()
          ? responseSummary.trim()
          : null;

      setResults(normalized);
      setTotal(response?.total ?? response?.data?.total ?? normalized.length);
      setTrends(Array.isArray(response?.trends) ? response.trends : []);
      setSummary(normalizedSummary);
      return {
        results: normalized,
        total: response?.total ?? normalized.length,
        trends: Array.isArray(response?.trends) ? response.trends : [],
        query: response?.query ?? query,
        fallback: false,
        summary: normalizedSummary,
      } as GlobalSearchResponse;
    } catch (err: any) {
      try {
        const fallbackResponse = await api.searchMessages(query, {
          limit,
          offset,
        });

        if (fallbackResponse?.success) {
          const normalized = dedupeResults(
            (fallbackResponse.messages || []).map((msg: any) =>
              normalizeResult({ ...msg, type: "message" }),
            ),
          );
          setResults(normalized);
          setTotal(fallbackResponse.results_count ?? normalized.length);
          setFallback(true);
          setSummary(null);
          return {
            results: normalized,
            total: fallbackResponse.results_count ?? normalized.length,
            trends: [],
            query,
            fallback: true,
            summary: null,
          } as GlobalSearchResponse;
        }
      } catch (fallbackError: any) {
        setError(fallbackError?.message || "Search failed");
      }

      setError(err?.message || "Search failed");
      setResults([]);
      setTotal(0);
      setTrends([]);
      setSummary(null);
      return {
        results: [],
        total: 0,
        trends: [],
        query,
        fallback: false,
        summary: null,
      } as GlobalSearchResponse;
    } finally {
      setLoading(false);
    }
  }, []);

  const clear = useCallback(() => {
    setResults([]);
    setTotal(0);
    setTrends([]);
    setError(null);
    setFallback(false);
    setSummary(null);
  }, []);

  return {
    results,
    total,
    trends,
    summary,
    loading,
    error,
    fallback,
    search,
    clear,
  };
}
