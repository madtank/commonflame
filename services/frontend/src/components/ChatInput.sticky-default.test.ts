import { describe, expect, it } from "vitest";

import {
  CONCIERGE_HANDLE,
  extractMentionUsernames,
  getKnownMentionedAgentUsernames,
  planStickyRouting,
  prependMentionToMessage,
  resolveMentionedAgentIds,
} from "./chat-input-routing";

describe("chat-input sticky routing helpers", () => {
  const agentIdMap = new Map<string, string>([
    ["react_ranger", "agent-react"],
    ["test_agent", "agent-test"],
    ["data_diva", "agent-data"],
  ]);

  it("extracts normalized mention usernames", () => {
    expect(
      extractMentionUsernames("@react_ranger please pair with @test_agent"),
    ).toEqual(["react_ranger", "test_agent"]);
  });

  it("prepends the default agent when an unmentioned message is routed", () => {
    expect(prependMentionToMessage("follow up question", "react_ranger")).toBe(
      "@react_ranger follow up question",
    );
    expect(
      prependMentionToMessage("@react_ranger already included", "react_ranger"),
    ).toBe("@react_ranger already included");
  });

  it("plans sticky routing for an initial explicit mention", () => {
    const result = planStickyRouting(
      "@react_ranger please review",
      null,
      agentIdMap,
    );

    expect(result.routedMessageBase).toBe("@react_ranger please review");
    expect(result.nextDefaultAgent).toBe("react_ranger");
    expect(result.knownMentionedAgentUsernames).toEqual(["react_ranger"]);
    expect(result.mentionedAgentIds).toEqual(["agent-react"]);
  });

  it("sticks to the current default agent for later unmentioned messages", () => {
    const result = planStickyRouting(
      "follow up question",
      "react_ranger",
      agentIdMap,
    );

    expect(result.routedMessageBase).toBe("@react_ranger follow up question");
    expect(result.nextDefaultAgent).toBeUndefined();
    expect(result.mentionedAgentIds).toEqual(["agent-react"]);
  });

  it("switches the default when a different agent is explicitly mentioned", () => {
    const result = planStickyRouting(
      "@test_agent switch over",
      "react_ranger",
      agentIdMap,
    );

    expect(result.routedMessageBase).toBe("@test_agent switch over");
    expect(result.nextDefaultAgent).toBe("test_agent");
    expect(result.mentionedAgentIds).toEqual(["agent-test"]);
  });

  it("treats an explicit concierge mention as a reset", () => {
    const result = planStickyRouting(
      `@${CONCIERGE_HANDLE} route via concierge`,
      "react_ranger",
      agentIdMap,
    );

    expect(result.routedMessageBase).toBe(
      `@${CONCIERGE_HANDLE} route via concierge`,
    );
    expect(result.nextDefaultAgent).toBeNull();
    expect(result.mentionedAgentIds).toEqual([]);
  });

  it("filters unknown mentions when resolving known agent ids", () => {
    const usernames = getKnownMentionedAgentUsernames(
      "@react_ranger and @unknown then @data_diva",
      agentIdMap,
    );

    expect(usernames).toEqual(["react_ranger", "data_diva"]);
    expect(resolveMentionedAgentIds(usernames, agentIdMap)).toEqual([
      "agent-react",
      "agent-data",
    ]);
  });
});
