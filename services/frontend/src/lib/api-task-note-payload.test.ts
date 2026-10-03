import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, apiClient } from "./api-clean";

describe("task note payload normalization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("maps legacy note field to backend content field and normalizes visibility", async () => {
    const postSpy = vi
      .spyOn(apiClient, "post")
      .mockResolvedValue({ data: { ok: true } } as any);

    await api.createTaskNote("task-123", {
      note: "legacy note",
      note_type: "progress",
      visibility: "org",
    });

    expect(postSpy).toHaveBeenCalledWith("/api/v1/tasks/task-123/notes", {
      content: "legacy note",
      note_type: "progress",
      visibility: "team",
    });
  });

  it("passes through content payload when already in backend schema", async () => {
    const postSpy = vi
      .spyOn(apiClient, "post")
      .mockResolvedValue({ data: { ok: true } } as any);

    await api.createTaskNote("task-456", {
      content: "direct content",
      note_type: "general",
      visibility: "public",
    });

    expect(postSpy).toHaveBeenCalledWith("/api/v1/tasks/task-456/notes", {
      content: "direct content",
      note_type: "general",
      visibility: "public",
    });
  });
});
