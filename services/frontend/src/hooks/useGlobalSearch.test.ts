import { describe, it, expect, afterEach, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useGlobalSearch } from "./useGlobalSearch";
import { api } from "@/lib/api-clean";

describe("useGlobalSearch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("dedupes results by type and id, keeping the best score", async () => {
    const results = [
      {
        type: "message",
        id: "msg-dup",
        content: "Duplicate payload that should only appear once.",
        author: "testuser",
        channel: "main",
        timestamp: "2025-07-21T10:00:00Z",
        score: 0.71,
      },
      {
        type: "message",
        id: "msg-dup",
        content: "Duplicate payload that should only appear once.",
        author: "testuser",
        channel: "main",
        timestamp: "2025-07-21T10:00:00Z",
        score: 0.88,
      },
    ];

    vi.spyOn(api, "searchGlobal").mockResolvedValue({
      results,
      total: results.length,
      query: "dup",
      trends: [],
    });

    const { result } = renderHook(() => useGlobalSearch());

    await act(async () => {
      await result.current.search({
        query: "dup",
        scope: "messages",
        limit: 20,
        offset: 0,
      });
    });

    await waitFor(() => {
      expect(result.current.results).toHaveLength(1);
    });

    expect(result.current.results[0].id).toBe("msg-dup");
    expect(result.current.results[0].score).toBe(0.88);
  });
});
