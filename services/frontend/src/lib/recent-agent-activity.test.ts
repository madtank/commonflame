import { describe, expect, it } from "vitest";

import { buildRecentAgentActivity } from "@/lib/recent-agent-activity";
import type { Post } from "@/types/message";

const NOW = Date.parse("2026-04-04T21:00:00.000Z");

function makePost(overrides: Partial<Post>): Post {
  return {
    id: overrides.id ?? Math.random().toString(),
    content: overrides.content ?? "test",
    uploaded_at: overrides.uploaded_at ?? "2026-04-04T20:30:00.000Z",
    created_at: overrides.created_at,
    username: overrides.username ?? "agent_one",
    sender_type: overrides.sender_type,
    author_type: overrides.author_type,
    agent_type: overrides.agent_type,
    metadata: overrides.metadata,
  };
}

describe("buildRecentAgentActivity", () => {
  it("sorts agent activity by most recent message in the last hour", () => {
    const posts: Post[] = [
      makePost({
        id: 1,
        username: "agent_alpha",
        sender_type: "agent",
        uploaded_at: "2026-04-04T20:15:00.000Z",
      }),
      makePost({
        id: 2,
        username: "agent_beta",
        sender_type: "agent",
        uploaded_at: "2026-04-04T20:45:00.000Z",
      }),
      makePost({
        id: 3,
        username: "person_user",
        sender_type: "user",
        uploaded_at: "2026-04-04T20:50:00.000Z",
      }),
    ];

    expect(buildRecentAgentActivity(posts, [], NOW)).toEqual([
      {
        username: "agent_beta",
        lastMessageAt: "2026-04-04T20:45:00.000Z",
        messageCount: 1,
      },
      {
        username: "agent_alpha",
        lastMessageAt: "2026-04-04T20:15:00.000Z",
        messageCount: 1,
      },
    ]);
  });

  it("counts last-hour messages for known agents even when sender_type is missing", () => {
    const posts: Post[] = [
      makePost({
        id: 1,
        username: "react_ranger",
        uploaded_at: "2026-04-04T20:40:00.000Z",
      }),
      makePost({
        id: 2,
        username: "react_ranger",
        uploaded_at: "2026-04-04T20:20:00.000Z",
      }),
      makePost({
        id: 3,
        username: "react_ranger",
        uploaded_at: "2026-04-04T18:20:00.000Z",
      }),
      makePost({
        id: 4,
        username: "helper_hawk",
        uploaded_at: "2026-04-04T20:10:00.000Z",
      }),
    ];

    expect(
      buildRecentAgentActivity(posts, ["react_ranger", "helper_hawk"], NOW),
    ).toEqual([
      {
        username: "react_ranger",
        lastMessageAt: "2026-04-04T20:40:00.000Z",
        messageCount: 3,
      },
      {
        username: "helper_hawk",
        lastMessageAt: "2026-04-04T20:10:00.000Z",
        messageCount: 1,
      },
    ]);
  });
});
