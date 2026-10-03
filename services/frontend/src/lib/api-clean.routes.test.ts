import { afterEach, describe, expect, it, vi } from "vitest";

import { api, apiClient } from "./api-clean";

describe("api-clean canonical read routes", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads spaces through the v1 spaces route", async () => {
    const getSpy = vi.spyOn(apiClient, "get").mockResolvedValue({
      data: { spaces: [{ id: "space-1", name: "Alpha" }] },
    } as any);

    await expect(api.getSpaces()).resolves.toEqual({
      spaces: [{ id: "space-1", name: "Alpha" }],
    });
    expect(getSpy).toHaveBeenCalledWith("/api/v1/spaces");
  });

  it("loads organization switcher data through the v1 spaces route", async () => {
    const getSpy = vi.spyOn(apiClient, "get").mockResolvedValue({
      data: { spaces: [{ id: "space-1", name: "Alpha" }] },
    } as any);

    await expect(api.getOrganizations()).resolves.toEqual([
      { id: "space-1", name: "Alpha" },
    ]);
    expect(getSpy).toHaveBeenCalledWith("/api/v1/spaces");
  });

  it("normalizes nested spaces envelopes and applies current_space_id", async () => {
    vi.spyOn(apiClient, "get").mockResolvedValue({
      data: {
        current_space_id: "space-2",
        data: {
          items: [
            { id: "space-1", name: "Alpha" },
            { id: "space-2", name: "Beta" },
          ],
        },
      },
    } as any);

    await expect(api.getSpaces()).resolves.toMatchObject({
      spaces: [
        { id: "space-1", name: "Alpha", is_current: false },
        { id: "space-2", name: "Beta", is_current: true },
      ],
    });
  });

  it("lets current_space_id from the database override stale item current flags", async () => {
    vi.spyOn(apiClient, "get").mockResolvedValue({
      data: {
        current_space_id: "db-current",
        spaces: [
          { id: "prediction-lab", name: "Prediction Lab", is_current: true },
          { id: "db-current", name: "DB Current Space", is_current: false },
        ],
      },
    } as any);

    await expect(api.getOrganizations()).resolves.toEqual([
      { id: "prediction-lab", name: "Prediction Lab", is_current: false },
      { id: "db-current", name: "DB Current Space", is_current: true },
    ]);
  });

  it("hydrates organization switcher from current_space when list is absent", async () => {
    vi.spyOn(apiClient, "get").mockResolvedValue({
      data: { current_space: { id: "space-current", name: "Current" } },
    } as any);

    await expect(api.getOrganizations()).resolves.toEqual([
      { id: "space-current", name: "Current", is_current: true },
    ]);
  });

  it("loads task lists through the v1 task read route", async () => {
    const getSpy = vi.spyOn(apiClient, "get").mockResolvedValue({
      data: { tasks: [{ id: "task-1" }], total: 1, limit: 50, offset: 0 },
    } as any);

    await expect(api.getTasks(50)).resolves.toEqual({
      tasks: [{ id: "task-1" }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    expect(getSpy).toHaveBeenCalledWith("/api/v1/tasks", {
      params: { limit: 50 },
    });
  });

  it("loads task detail through the v1 task read route", async () => {
    const getSpy = vi
      .spyOn(apiClient, "get")
      .mockResolvedValue({ data: { task: { id: "task-1" } } } as any);

    await expect(api.getTask("task-1")).resolves.toEqual({
      task: { id: "task-1" },
    });
    expect(getSpy).toHaveBeenCalledWith("/api/v1/tasks/task-1");
  });

  it("switches organizations through the canonical space switch route", async () => {
    const postSpy = vi
      .spyOn(apiClient, "post")
      .mockResolvedValue({ data: { new_token: "token-1" } } as any);

    await expect(api.switchOrganization("space-1")).resolves.toEqual({
      new_token: "token-1",
    });
    expect(postSpy).toHaveBeenCalledWith("/api/spaces/switch", {
      space_id: "space-1",
    });
  });

  it("switches spaces without falling back to legacy organization routes", async () => {
    const postSpy = vi
      .spyOn(apiClient, "post")
      .mockResolvedValue({ data: { new_token: "token-1" } } as any);

    await expect(api.switchSpace("space-1")).resolves.toEqual({
      new_token: "token-1",
    });
    expect(postSpy).toHaveBeenCalledOnce();
    expect(postSpy).toHaveBeenCalledWith("/api/spaces/switch", {
      space_id: "space-1",
    });
  });

  it("loads channels through the canonical messages API route", async () => {
    const getSpy = vi
      .spyOn(apiClient, "get")
      .mockResolvedValue({ data: ["main", "dev"] } as any);

    await expect(api.getAvailableChannels()).resolves.toEqual(["main", "dev"]);
    expect(getSpy).toHaveBeenCalledWith("/api/channels");
  });

  it("adds emoji reactions through the canonical message reactions route", async () => {
    const postSpy = vi
      .spyOn(apiClient, "post")
      .mockResolvedValue({ data: { ok: true } } as any);

    await expect(api.reactToPost(123, "👍")).resolves.toEqual({ ok: true });
    expect(postSpy).toHaveBeenCalledWith("/api/v1/messages/123/reactions", {
      emoji: "👍",
    });
  });

  it("does not call legacy /posts routes for stale reaction read helpers", async () => {
    const getSpy = vi
      .spyOn(apiClient, "get")
      .mockResolvedValue({ data: { reactions: { "👍": 2 } } } as any);
    const postSpy = vi.spyOn(apiClient, "post");

    await expect(api.getPostReactions(123)).resolves.toEqual({
      reaction_counts: { "👍": 2 },
    });
    await expect(api.getBatchPostReactions([123, 456])).resolves.toEqual({
      reaction_counts: { "123": {}, "456": {} },
      reactions: { "123": {}, "456": {} },
    });

    expect(getSpy).toHaveBeenCalledWith("/api/messages/123");
    expect(postSpy).not.toHaveBeenCalled();
  });
});
