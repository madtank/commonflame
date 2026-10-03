/**
 * Tests for AgentBaseballCard component.
 *
 * Spec: WIDGET-002 §5.1 — "Hide or transform: backend enum names"
 * Agent types like "EXTERNAL_GATEWAY" must render as human-readable labels.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { AgentBaseballCard } from "./AgentBaseballCard";

const baseStats = {
  totalMessages: 100,
  tasksCompleted: 50,
  tasksInProgress: 5,
  averageResponseTime: "2.3s",
  successRate: 95,
  daysActive: 30,
  lastActive: "2h ago",
};

function makeAgent(
  overrides: Partial<{ agent_type: string; name: string; status: string }> = {},
) {
  return {
    id: "agent-1",
    name: "Test Agent",
    agent_type: "EXTERNAL_GATEWAY",
    status: "active" as const,
    ...overrides,
  };
}

describe("AgentBaseballCard", () => {
  it("renders humanized agent type instead of raw enum", () => {
    render(
      <AgentBaseballCard
        agent={makeAgent({ agent_type: "EXTERNAL_GATEWAY" })}
        stats={baseStats}
      />,
    );

    expect(screen.getByText("External Gateway")).toBeInTheDocument();
    expect(screen.queryByText("EXTERNAL_GATEWAY")).not.toBeInTheDocument();
  });

  it("renders SPACE_AGENT as 'Space Agent'", () => {
    render(
      <AgentBaseballCard
        agent={makeAgent({ agent_type: "SPACE_AGENT" })}
        stats={baseStats}
      />,
    );

    expect(screen.getByText("Space Agent")).toBeInTheDocument();
    expect(screen.queryByText("SPACE_AGENT")).not.toBeInTheDocument();
  });

  it("renders CLI as 'CLI' (known abbreviation)", () => {
    render(
      <AgentBaseballCard
        agent={makeAgent({ agent_type: "CLI" })}
        stats={baseStats}
      />,
    );

    expect(screen.getByText("CLI")).toBeInTheDocument();
  });

  it("renders MCP as 'MCP' (known abbreviation)", () => {
    render(
      <AgentBaseballCard
        agent={makeAgent({ agent_type: "MCP" })}
        stats={baseStats}
      />,
    );

    expect(screen.getByText("MCP")).toBeInTheDocument();
  });
});
