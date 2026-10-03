import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, type RosterEntry } from "@/lib/api-clean";

type RosterOptions = {
  type?: "human" | "agent";
  search?: string;
  limit?: number;
  offset?: number;
};

type LookupFn = (handleOrId: string) => RosterEntry | undefined;

const normalizeHandle = (value: string): string =>
  value.replace(/^@/, "").trim().toLowerCase();

export function useRoster(
  orgId: string | null | undefined,
  options: RosterOptions = {},
) {
  const [entries, setEntries] = useState<RosterEntry[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const { type, search, limit = 200, offset = 0 } = options;

  const fetchRoster = useCallback(async () => {
    if (!orgId) {
      setEntries([]);
      setTotal(0);
      setIsLoading(false);
      setError(null);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    setError(null);

    try {
      const data = await api.fetchRoster(orgId, {
        type,
        search,
        limit,
        offset,
      });

      if (!controller.signal.aborted) {
        setEntries(data.items || []);
        setTotal(data.total ?? 0);
      }
    } catch (err: any) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err : new Error(String(err)));
        setEntries([]);
        setTotal(0);
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsLoading(false);
      }
    }
  }, [orgId, type, search, limit, offset]);

  useEffect(() => {
    fetchRoster();

    return () => {
      abortRef.current?.abort();
    };
  }, [fetchRoster]);

  const lookupMaps = useMemo(() => {
    const byId = new Map<string, RosterEntry>();
    const byHandle = new Map<string, RosterEntry>();

    entries.forEach((entry) => {
      byId.set(entry.id, entry);
      if (entry.handle) {
        byHandle.set(normalizeHandle(entry.handle), entry);
      }
    });

    return { byId, byHandle };
  }, [entries]);

  const lookup: LookupFn = useCallback(
    (handleOrId: string) => {
      if (!handleOrId) return undefined;
      const trimmed = handleOrId.trim();

      if (lookupMaps.byId.has(trimmed)) {
        return lookupMaps.byId.get(trimmed);
      }

      return lookupMaps.byHandle.get(normalizeHandle(trimmed));
    },
    [lookupMaps.byHandle, lookupMaps.byId],
  );

  const refresh = useCallback(() => {
    fetchRoster();
  }, [fetchRoster]);

  return {
    entries,
    total,
    isLoading,
    error,
    refresh,
    lookupByHandle: lookup,
  } as const;
}

export type { RosterEntry };
