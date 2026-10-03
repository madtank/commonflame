import { afterEach, describe, expect, it, vi } from "vitest";

import { api, apiClient } from "./api-clean";

describe("api.getUserAgents", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rethrows backend errors so callers can render explicit endpoint states", async () => {
    const error = { response: { status: 404 } };

    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const getSpy = vi.spyOn(apiClient, "get").mockImplementation(async () => {
      throw error;
    });

    await expect(api.getUserAgents({ owner: "me" })).rejects.toBe(error);
    expect(getSpy).toHaveBeenCalledWith("/auth/agents?owner=me");
  });
});
