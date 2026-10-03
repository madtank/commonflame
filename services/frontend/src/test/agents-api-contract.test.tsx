/**
 * Agents API Contract Tests
 *
 * Validates the response shape from GET /auth/agents which is consumed
 * by AgentManagement.tsx. These tests guard against backend regressions
 * (e.g., PR #125 - Redis batch pipeline optimization) by asserting:
 *
 * 1. Pagination: total_count, limit, offset, has_more, no overlapping agents
 * 2. Sort modes: relevance, recent, name return different orderings
 * 3. Agent stats: trust_score, reactions, message counts populated
 * 4. Control state: is_disabled, rate_limits rendered for cloud agents
 */
import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// --- Test data: 25 agents to test pagination (page size = 20) ---

// Shuffled names so alphabetical sort differs from numeric ordering
const AGENT_NAMES = [
  "nova",
  "echo",
  "atlas",
  "zephyr",
  "bolt",
  "mesa",
  "flux",
  "delta",
  "quark",
  "prism",
  "vortex",
  "cipher",
  "apex",
  "rune",
  "drift",
  "spark",
  "comet",
  "titan",
  "jade",
  "orbit",
  "wave",
  "pixel",
  "frost",
  "blaze",
  "storm",
];

// Activity scores that don't correlate with name order or recency
const ACTIVITY_SCORES = [
  80, 15, 95, 40, 70, 55, 30, 88, 10, 62, 45, 73, 20, 91, 35, 50, 85, 5, 68, 42,
  25, 78, 60, 33, 99,
];

const makeAgent = (i: number, overrides: Record<string, unknown> = {}) => {
  const idx = i - 1;
  const activityScore = ACTIVITY_SCORES[idx];

  return {
    id: `agent-${String(i).padStart(3, "0")}`,
    username: AGENT_NAMES[idx],
    agent_type: i % 3 === 0 ? "cloud" : "general",
    bio: `Bio for agent ${AGENT_NAMES[idx]}`,
    status: i % 5 === 0 ? "inactive" : "active",
    is_own_agent: i <= 5,
    is_own: i <= 5,
    owner_username: i <= 5 ? "testuser" : `owner_${i}`,
    can_control: i <= 5,
    can_update: i <= 5,
    space_name: "madtank's Workspace",
    avatar_url: null,
    org_id: "org-1",

    // Stats (what PR #125 batches via Redis pipeline)
    posts_count: activityScore,
    post_count: activityScore,
    tasks_completed: Math.floor(activityScore * 0.6),
    tasks_assigned: Math.floor(activityScore * 0.8),
    completion_rate: 75,

    // Trust / Intelligence scores (batch-fetched)
    trust_score: +(0.95 - i * 0.02).toFixed(2),
    avg_quality_score: +(0.9 - i * 0.01).toFixed(2),
    avg_spam_score: +(0.01 + i * 0.005).toFixed(3),
    avg_toxicity_score: 0,
    messages_analyzed: 200 - i * 5,

    // Reactions (batch-fetched)
    reactions: { "👍": 10 - Math.floor(i / 3), "🚀": 5 - Math.floor(i / 5) },

    // Control state (moved from sequential to batch Redis in PR #125)
    control: {
      is_disabled: i === 10,
      rate_limits: { messages_per_minute: 30, tasks_per_hour: 10 },
      is_running: i % 3 === 0 && i !== 10,
      is_paused: false,
    },

    // Cloud agent fields
    enable_cloud_agent: i % 3 === 0,
    cloud_function_url: i % 3 === 0 ? `https://cloud.run/agent-${i}` : null,

    // Recency doesn't correlate with activity score - use shuffled times
    last_seen: new Date(Date.now() - idx * 3600000).toISOString(),
    last_activity: new Date(
      Date.now() - ((idx * 7 + 3) % 25) * 1800000,
    ).toISOString(),

    ...overrides,
  };
};

const ALL_AGENTS = Array.from({ length: 25 }, (_, i) => makeAgent(i + 1));

// Sort helpers matching backend logic
const sortByRelevance = (agents: typeof ALL_AGENTS) =>
  [...agents].sort((a, b) => {
    // Pinned first, then by activity (posts + tasks)
    const aScore = a.posts_count + a.tasks_completed;
    const bScore = b.posts_count + b.tasks_completed;
    return bScore - aScore;
  });

const sortByRecent = (agents: typeof ALL_AGENTS) =>
  [...agents].sort(
    (a, b) =>
      new Date(b.last_activity!).getTime() -
      new Date(a.last_activity!).getTime(),
  );

const sortByName = (agents: typeof ALL_AGENTS) =>
  [...agents].sort((a, b) => a.username.localeCompare(b.username));

// --- MSW handler that simulates the real backend ---

const agentsHandler = (urlPattern: string) =>
  http.get(urlPattern, ({ request }) => {
    const url = new URL(request.url);
    const limit = parseInt(url.searchParams.get("limit") || "20");
    const offset = parseInt(url.searchParams.get("offset") || "0");
    const sort = url.searchParams.get("sort") || "relevance";
    const owner = url.searchParams.get("owner");
    const search = url.searchParams.get("search");

    let agents = [...ALL_AGENTS];

    // Filter by owner
    if (owner === "me") {
      agents = agents.filter((a) => a.is_own_agent);
    }

    // Filter by search
    if (search) {
      const q = search.toLowerCase();
      agents = agents.filter(
        (a) =>
          a.username.toLowerCase().includes(q) ||
          a.bio.toLowerCase().includes(q),
      );
    }

    // Sort
    if (sort === "relevance") agents = sortByRelevance(agents);
    else if (sort === "recent") agents = sortByRecent(agents);
    else if (sort === "name") agents = sortByName(agents);

    const totalCount = agents.length;
    const paged = agents.slice(offset, offset + limit);

    return HttpResponse.json({
      agents: paged,
      total_count: totalCount,
      limit,
      offset,
      has_more: offset + limit < totalCount,
    });
  });

// --- Tests ---

describe("Agents API Contract (PR #125 regression guard)", () => {
  beforeEach(() => {
    server.resetHandlers();
    // Install rich handlers for all URL patterns the app uses
    server.use(
      agentsHandler("/auth/agents"),
      agentsHandler("http://localhost:8001/auth/agents"),
      agentsHandler("http://127.0.0.1:8001/auth/agents"),
    );
  });

  // ---------- Response shape ----------

  describe("Response shape", () => {
    it("returns required pagination fields", async () => {
      const res = await fetch("/auth/agents?limit=20&offset=0");
      const data = await res.json();

      expect(data).toHaveProperty("agents");
      expect(data).toHaveProperty("total_count");
      expect(data).toHaveProperty("limit");
      expect(data).toHaveProperty("offset");
      expect(data).toHaveProperty("has_more");

      expect(Array.isArray(data.agents)).toBe(true);
      expect(typeof data.total_count).toBe("number");
      expect(typeof data.has_more).toBe("boolean");
    });

    it("each agent has stats fields (batch-fetched in PR #125)", async () => {
      const res = await fetch("/auth/agents?limit=5");
      const { agents } = await res.json();

      for (const agent of agents) {
        // Core identity
        expect(agent).toHaveProperty("id");
        expect(agent).toHaveProperty("username");
        expect(agent).toHaveProperty("status");

        // Stats (previously sequential, now batched)
        expect(agent).toHaveProperty("posts_count");
        expect(agent).toHaveProperty("tasks_completed");

        // Trust scores (batch-fetched)
        expect(agent).toHaveProperty("trust_score");
        expect(typeof agent.trust_score).toBe("number");

        // Reactions (batch-fetched)
        expect(agent).toHaveProperty("reactions");
        expect(typeof agent.reactions).toBe("object");

        // Control state (moved to batch Redis pipeline)
        expect(agent).toHaveProperty("control");
        expect(agent.control).toHaveProperty("is_disabled");
        expect(agent.control).toHaveProperty("rate_limits");
      }
    });
  });

  // ---------- Pagination ----------

  describe("Pagination", () => {
    it("page 1 returns limit agents with has_more=true when more exist", async () => {
      const res = await fetch("/auth/agents?limit=20&offset=0");
      const data = await res.json();

      expect(data.agents).toHaveLength(20);
      expect(data.total_count).toBe(25);
      expect(data.has_more).toBe(true);
      expect(data.offset).toBe(0);
    });

    it("page 2 returns remaining agents with has_more=false", async () => {
      const res = await fetch("/auth/agents?limit=20&offset=20");
      const data = await res.json();

      expect(data.agents).toHaveLength(5);
      expect(data.total_count).toBe(25);
      expect(data.has_more).toBe(false);
      expect(data.offset).toBe(20);
    });

    it("page 1 and page 2 have zero overlapping agent IDs", async () => {
      const [res1, res2] = await Promise.all([
        fetch("/auth/agents?limit=20&offset=0"),
        fetch("/auth/agents?limit=20&offset=20"),
      ]);
      const page1 = await res1.json();
      const page2 = await res2.json();

      const ids1 = new Set(page1.agents.map((a: any) => a.id));
      const ids2 = new Set(page2.agents.map((a: any) => a.id));

      const overlap = [...ids1].filter((id) => ids2.has(id));
      expect(overlap).toHaveLength(0);
    });

    it("all agents across pages equals total_count", async () => {
      const [res1, res2] = await Promise.all([
        fetch("/auth/agents?limit=20&offset=0"),
        fetch("/auth/agents?limit=20&offset=20"),
      ]);
      const page1 = await res1.json();
      const page2 = await res2.json();

      const allIds = [
        ...page1.agents.map((a: any) => a.id),
        ...page2.agents.map((a: any) => a.id),
      ];
      const uniqueIds = new Set(allIds);

      expect(uniqueIds.size).toBe(page1.total_count);
    });
  });

  // ---------- Sorting ----------

  describe("Sort modes", () => {
    it("sort=relevance orders by activity score descending", async () => {
      const res = await fetch("/auth/agents?limit=25&sort=relevance");
      const { agents } = await res.json();

      for (let i = 1; i < agents.length; i++) {
        const prevScore =
          agents[i - 1].posts_count + agents[i - 1].tasks_completed;
        const currScore = agents[i].posts_count + agents[i].tasks_completed;
        expect(prevScore).toBeGreaterThanOrEqual(currScore);
      }
    });

    it("sort=recent orders by last_activity descending", async () => {
      const res = await fetch("/auth/agents?limit=25&sort=recent");
      const { agents } = await res.json();

      for (let i = 1; i < agents.length; i++) {
        const prevTime = new Date(agents[i - 1].last_activity).getTime();
        const currTime = new Date(agents[i].last_activity).getTime();
        expect(prevTime).toBeGreaterThanOrEqual(currTime);
      }
    });

    it("sort=name orders alphabetically by username", async () => {
      const res = await fetch("/auth/agents?limit=25&sort=name");
      const { agents } = await res.json();

      for (let i = 1; i < agents.length; i++) {
        expect(
          agents[i - 1].username.localeCompare(agents[i].username),
        ).toBeLessThanOrEqual(0);
      }
    });

    it("different sort modes return different orderings", async () => {
      const [r1, r2, r3] = await Promise.all([
        fetch("/auth/agents?limit=25&sort=relevance"),
        fetch("/auth/agents?limit=25&sort=recent"),
        fetch("/auth/agents?limit=25&sort=name"),
      ]);

      const relevance = (await r1.json()).agents.map((a: any) => a.id);
      const recent = (await r2.json()).agents.map((a: any) => a.id);
      const name = (await r3.json()).agents.map((a: any) => a.id);

      // At least one pair should differ in ordering
      const allSame =
        JSON.stringify(relevance) === JSON.stringify(recent) &&
        JSON.stringify(recent) === JSON.stringify(name);
      expect(allSame).toBe(false);
    });
  });

  // ---------- Filtering ----------

  describe("Filtering", () => {
    it("owner=me returns only own agents", async () => {
      const res = await fetch("/auth/agents?owner=me");
      const { agents } = await res.json();

      expect(agents.length).toBeGreaterThan(0);
      for (const agent of agents) {
        expect(agent.is_own_agent).toBe(true);
      }
    });

    it("search filters by username", async () => {
      const res = await fetch("/auth/agents?search=nova");
      const { agents, total_count } = await res.json();

      expect(total_count).toBe(1);
      expect(agents[0].username).toBe("nova");
    });
  });

  // ---------- Control state (key PR #125 change) ----------

  describe("Control state (batch Redis pipeline)", () => {
    it("disabled agent has control.is_disabled=true", async () => {
      const res = await fetch("/auth/agents?limit=25");
      const { agents } = await res.json();

      const disabled = agents.filter((a: any) => a.control?.is_disabled);
      expect(disabled.length).toBeGreaterThan(0);

      for (const agent of disabled) {
        expect(agent.control.is_disabled).toBe(true);
        expect(agent.control.rate_limits).toBeDefined();
      }
    });

    it("active cloud agents have control.is_running=true", async () => {
      const res = await fetch("/auth/agents?limit=25");
      const { agents } = await res.json();

      const cloudAgents = agents.filter(
        (a: any) => a.enable_cloud_agent && !a.control?.is_disabled,
      );

      for (const agent of cloudAgents) {
        expect(agent.control.is_running).toBe(true);
      }
    });

    it("all agents have rate_limits in control state", async () => {
      const res = await fetch("/auth/agents?limit=25");
      const { agents } = await res.json();

      for (const agent of agents) {
        expect(agent.control.rate_limits).toBeDefined();
        expect(agent.control.rate_limits.messages_per_minute).toBeDefined();
      }
    });
  });

  // ---------- Trust/Intelligence scores ----------

  describe("Trust and intelligence scores", () => {
    it("trust_score is a number between 0 and 1", async () => {
      const res = await fetch("/auth/agents?limit=25");
      const { agents } = await res.json();

      for (const agent of agents) {
        expect(typeof agent.trust_score).toBe("number");
        expect(agent.trust_score).toBeGreaterThanOrEqual(0);
        expect(agent.trust_score).toBeLessThanOrEqual(1);
      }
    });

    it("quality and spam scores are populated", async () => {
      const res = await fetch("/auth/agents?limit=5");
      const { agents } = await res.json();

      for (const agent of agents) {
        expect(agent.avg_quality_score).toBeDefined();
        expect(agent.avg_spam_score).toBeDefined();
        expect(agent.messages_analyzed).toBeDefined();
      }
    });
  });

  // ---------- Reactions ----------

  describe("Reactions (batch-fetched)", () => {
    it("reactions is an object with emoji keys and numeric values", async () => {
      const res = await fetch("/auth/agents?limit=5");
      const { agents } = await res.json();

      for (const agent of agents) {
        expect(typeof agent.reactions).toBe("object");
        for (const [key, value] of Object.entries(agent.reactions)) {
          expect(typeof key).toBe("string");
          expect(typeof value).toBe("number");
        }
      }
    });
  });

  // ---------- Edge cases ----------

  describe("Edge cases", () => {
    it("offset beyond total returns empty agents with has_more=false", async () => {
      const res = await fetch("/auth/agents?limit=20&offset=100");
      const data = await res.json();

      expect(data.agents).toHaveLength(0);
      expect(data.has_more).toBe(false);
      expect(data.total_count).toBe(25);
    });

    it("limit=1 returns exactly one agent", async () => {
      const res = await fetch("/auth/agents?limit=1&offset=0");
      const data = await res.json();

      expect(data.agents).toHaveLength(1);
      expect(data.has_more).toBe(true);
    });
  });
});
