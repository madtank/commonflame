import { describe, expect, it } from "vitest";

import {
  buildQuickActionGroupOptions,
  buildQuickActionRowAgents,
} from "./ChatInput";

describe("ChatInput pinned quick actions", () => {
  const agents = [
    {
      id: "agent-canary",
      username: "canary",
      agent_type: "frontend",
      org_id: "space-1",
      team_id: "core-ui",
      team_name: "Core UI",
      team_color: "#22d3ee",
    },
    {
      id: "agent-daimon",
      username: "daimon",
      agent_type: "review",
      org_id: "space-1",
      team_id: "core-ui",
      team_name: "Core UI",
      team_color: "#22d3ee",
    },
    {
      id: "agent-nyx",
      username: "nyx",
      agent_type: "backend",
      org_id: "space-1",
      team_id: "platform",
      team_name: "Platform",
      team_color: "#a78bfa",
    },
    {
      id: "agent-other",
      username: "other_space",
      agent_type: "backend",
      org_id: "space-2",
      team_id: "platform",
      team_name: "Platform",
      team_color: "#a78bfa",
    },
  ];

  it("builds selectable group quick actions with visible in-space members", () => {
    expect(
      buildQuickActionGroupOptions(
        [
          {
            id: "core-ui",
            name: "Core UI",
            description: "Frontend owners",
            color: "#22d3ee",
            agents: [],
          },
          {
            id: "platform",
            name: "Platform",
            description: "Backend owners",
            color: "#a78bfa",
            agents: [],
          },
        ],
        agents,
        "space-1",
      ),
    ).toEqual([
      {
        id: "core-ui",
        name: "Core UI",
        color: "#22d3ee",
        members: ["canary", "daimon"],
      },
      {
        id: "platform",
        name: "Platform",
        color: "#a78bfa",
        members: ["nyx"],
      },
    ]);
  });

  it("does not pin the single already-selected route as the first quick action", () => {
    expect(
      buildQuickActionRowAgents(
        {
          username: "canary",
          agent_type: "frontend",
          color: "#22d3ee",
          activityScore: 100,
          frequencyScore: 100,
          isCurrentRoute: true,
        },
        [
          {
            username: "canary",
            agent_type: "frontend",
            color: "#22d3ee",
            activityScore: 100,
            frequencyScore: 100,
          },
          {
            username: "daimon",
            agent_type: "review",
            color: "#f59e0b",
            activityScore: 90,
            frequencyScore: 10,
          },
          {
            username: "peach",
            agent_type: "operator",
            color: "#fb7185",
            activityScore: 80,
            frequencyScore: 5,
          },
        ],
      ).map((agent) => agent.username),
    ).toEqual(["daimon", "peach"]);
  });
});
