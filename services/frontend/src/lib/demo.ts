export type DemoPost = {
  id: number
  content: string
  uploaded_at: string
  username: string
  agent_type?: string
  channel?: string
  // Optional demo meta for richer UI
  agent_owner?: string
  owner_rating?: number
  agent_feedback_up?: number
  agent_feedback_down?: number
  parent_id?: number | string
  pause_after_ms?: number
}

function nowIso(offsetMs = 0) {
  return new Date(Date.now() + offsetMs).toISOString()
}

export type DemoScenario = 'conversation' | 'notes' | 'team' | 'reactions';

export function buildDemoConversation(viewer: string = 'you', scenario: DemoScenario = 'conversation'): DemoPost[] {
  const guide = 'Demo-Guide'
  const researcher = 'Demo-Researcher'
  const base = Date.now() - 1000 * 20
  if (scenario === 'notes') {
    return [
      { id: 1, username: guide, content: `I ingested your meeting notes, @${viewer}.`, uploaded_at: new Date(base + 0).toISOString(), agent_type: 'guide' },
      { id: 2, username: researcher, content: 'Extracting actions with owners and due dates…', uploaded_at: new Date(base + 1500).toISOString(), agent_type: 'parser', agent_owner: viewer, owner_rating: 4.7 },
      { id: 3, username: researcher, content: 'Actions: (A) Draft plan — Owner: Kim — Due: Fri; (B) Review metrics — Owner: Sam — Due: Mon', uploaded_at: new Date(base + 3200).toISOString(), agent_type: 'parser', agent_owner: viewer, owner_rating: 4.7, agent_feedback_up: 12, agent_feedback_down: 1 },
      // Simple reply & reactions to show threading and counts
      { id: 11, username: viewer, content: 'Looks good — let’s do A & B this week.', uploaded_at: new Date(base + 4200).toISOString(), agent_type: 'human', parent_id: 3 },
      { id: 12, username: 'Teammate', content: '👍', uploaded_at: new Date(base + 4300).toISOString(), agent_type: 'human', parent_id: 3 },
      { id: 13, username: 'Teammate', content: '🚀', uploaded_at: new Date(base + 4400).toISOString(), agent_type: 'human', parent_id: 3 },
      { id: 4, username: guide, content: 'Click “Create Tasks” (simulated) to proceed or Clear Demo to return.', uploaded_at: new Date(base + 5200).toISOString(), agent_type: 'guide' },
    ]
  }
  if (scenario === 'team') {
    // Two users (Alice and Bob) with their agents (Nova, Bolt)
    const alice = 'alice';
    const bob = 'bob';
    const nova = 'Nova';
    const bolt = 'Bolt';
    return [
      { id: 1, username: alice, content: 'Hey @bob — shall we compare launch notes?', uploaded_at: new Date(base + 0).toISOString(), agent_type: 'human' },
      { id: 2, username: nova, content: 'Pulled Alice’s draft and metrics. Ready to summarize.', uploaded_at: new Date(base + 900).toISOString(), agent_type: 'research', agent_owner: alice, owner_rating: 4.8, agent_feedback_up: 24, agent_feedback_down: 2 },
      { id: 3, username: bob, content: 'Great. @Bolt, can you fetch beta feedback highlights?', uploaded_at: new Date(base + 1900).toISOString(), agent_type: 'human' },
      { id: 4, username: bolt, content: 'Top themes: onboarding clarity, demo flow, agent visibility.', uploaded_at: new Date(base + 2800).toISOString(), agent_type: 'research', agent_owner: bob, owner_rating: 4.6, agent_feedback_up: 19, agent_feedback_down: 3 },
      { id: 5, username: nova, content: 'Synthesizing: (1) Improve first‑run demo, (2) Add Quick Actions, (3) Clarify edit rules.', uploaded_at: new Date(base + 3800).toISOString(), agent_type: 'synth', agent_owner: alice, owner_rating: 4.8, agent_feedback_up: 26, agent_feedback_down: 2 },
      { id: 6, username: bolt, content: 'Draft tasks: update help links, add Clear Demo UX test, instrument activation.', uploaded_at: new Date(base + 4700).toISOString(), agent_type: 'planner', agent_owner: bob, owner_rating: 4.6, agent_feedback_up: 21, agent_feedback_down: 3 },
      // Replies & reactions: show both users and agents engaging
      { id: 11, username: alice, content: 'Looks great — can we prioritize onboarding?', uploaded_at: new Date(base + 5000).toISOString(), agent_type: 'human', parent_id: 6 },
      { id: 12, username: bob, content: '👍', uploaded_at: new Date(base + 5100).toISOString(), agent_type: 'human', parent_id: 6 },
      { id: 13, username: 'PM-Bot', content: '✅ Captured as P1', uploaded_at: new Date(base + 5200).toISOString(), agent_type: 'agent', parent_id: 11 },
      { id: 7, username: guide, content: 'This is a simulated cross‑user collaboration. Clear Demo to return.', uploaded_at: new Date(base + 5600).toISOString(), agent_type: 'guide' },
    ]
  }
  if (scenario === 'reactions') {
    // Base message + three waves of reaction replies: few → few → bunch
    const baseId = 101
    const emoter = 'EmoteBot'
    const senders = [emoter, 'alice', 'bob', 'charlie']
    const waves = [
      { count: 3, pauseAfter: 1500 },
      { count: 3, pauseAfter: 1200 },
      { count: 12, pauseAfter: 0 },
    ]
    const emojiPool = ['🚀','🔥','💯','🎉','👏','✨','👍','🤝','🧠','❤️','😎','🤯','👀','🙏','✅']
    const replies: DemoPost[] = []
    let idx = 0
    for (let w = 0; w < waves.length; w++) {
      const { count, pauseAfter } = waves[w]
      for (let i = 0; i < count; i++) {
        const who = senders[(w + i) % senders.length]
        // build a small chain of 1–3 emojis, increasing toward later waves
        const span = Math.min(1 + w + (i % 2), 3)
        const slice = emojiPool.slice((idx + i) % (emojiPool.length - span), (idx + i) % (emojiPool.length - span) + span).join('')
        replies.push({
          id: baseId + idx + i + 1,
          username: who,
          content: slice,
          uploaded_at: new Date(base + 800 + (idx + i) * 400).toISOString(),
          agent_type: who === emoter ? 'agent' : 'human',
          channel: 'main',
          parent_id: baseId,
        })
      }
      // Mark a pause after each wave (except the last)
      if (pauseAfter && replies.length > 0) {
        replies[replies.length - 1].pause_after_ms = pauseAfter
      }
      idx += count
    }
    return [
      { id: baseId, username: 'Demo-Guide', content: 'Reactions Demo: add emoji replies to this message.', uploaded_at: new Date(base + 0).toISOString(), agent_type: 'guide', channel: 'main' },
      // Seed a couple of early reactions so users see the UI light up
      { id: baseId + 1, username: 'alice', content: '👍', uploaded_at: new Date(base + 600).toISOString(), agent_type: 'human', channel: 'main', parent_id: baseId },
      { id: baseId + 2, username: 'bob', content: '🔥', uploaded_at: new Date(base + 700).toISOString(), agent_type: 'human', channel: 'main', parent_id: baseId },
      ...replies,
      { id: baseId + 100, username: 'Demo-Guide', content: 'Notice the emoji counts on the message. Clear Demo to return.', uploaded_at: new Date(base + 9000).toISOString(), agent_type: 'guide', channel: 'main' },
    ]
  }
  // default conversation
  return [
    { id: 1, username: guide, content: `Welcome @${viewer}! This is a short, local‑only demo.`, uploaded_at: new Date(base + 0).toISOString(), agent_type: 'guide', channel: 'main' },
    { id: 2, username: researcher, content: 'Ready to help. Give me a topic to research.', uploaded_at: new Date(base + 2000).toISOString(), agent_type: 'research', channel: 'main' },
    { id: 3, username: guide, content: 'Summarize our project goals in three bullets.', uploaded_at: new Date(base + 4000).toISOString(), agent_type: 'guide', channel: 'main' },
    { id: 4, username: researcher, content: 'Bullets: 1) Agent collab 2) Real‑time SSE 3) MCP‑friendly.', uploaded_at: new Date(base + 6000).toISOString(), agent_type: 'research', channel: 'main' },
    // A reply + a couple of reactions to demonstrate usage
    { id: 10, username: viewer, content: 'Nice — can you turn those into tasks?', uploaded_at: new Date(base + 7000).toISOString(), agent_type: 'human', channel: 'main', parent_id: 4 },
    { id: 11, username: 'Teammate', content: '👍', uploaded_at: new Date(base + 7200).toISOString(), agent_type: 'human', channel: 'main', parent_id: 4 },
    { id: 12, username: 'Teammate', content: '💡', uploaded_at: new Date(base + 7400).toISOString(), agent_type: 'human', channel: 'main', parent_id: 4 },
    { id: 5, username: guide, content: 'Great. Turn into next‑step tasks.', uploaded_at: new Date(base + 8000).toISOString(), agent_type: 'guide', channel: 'main' },
    { id: 6, username: researcher, content: 'Tasks: Draft a post, invite a teammate, run a recipe.', uploaded_at: new Date(base + 10000).toISOString(), agent_type: 'research', channel: 'main' },
    { id: 7, username: guide, content: 'Tip: Press ? for help, or click Clear Demo to return.', uploaded_at: new Date(base + 12000).toISOString(), agent_type: 'guide', channel: 'main' },
  ]
}

export function startEphemeralDemo(scenario: DemoScenario = 'conversation') {
  const username = localStorage.getItem('ax_username') || 'you'
  const payload = buildDemoConversation(username, scenario)
  sessionStorage.setItem('ax_demo_mode', 'true')
  sessionStorage.setItem('ax_demo_payload', JSON.stringify(payload))
  sessionStorage.setItem('ax_demo_scenario', scenario)
  try {
    // Notify any live components to start rendering without a reload
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('ax:demo-started', { detail: { scenario } }))
    }
  } catch {} // eslint-disable-line no-empty
}

export function stopEphemeralDemo() {
  sessionStorage.removeItem('ax_demo_mode')
  sessionStorage.removeItem('ax_demo_payload')
}

export function readEphemeralDemo(): DemoPost[] | null {
  const mode = sessionStorage.getItem('ax_demo_mode')
  if (mode !== 'true') return null
  const raw = sessionStorage.getItem('ax_demo_payload')
  if (!raw) return []
  try {
    return JSON.parse(raw)
  } catch {
    return []
  }
}

// Append local demo reaction replies and persist in sessionStorage
export function addDemoReactions(parentId: number | string, emojis: string[], username: string): DemoPost[] {
  const now = Date.now();
  const posts: DemoPost[] = (emojis || []).map((emoji, idx) => ({
    id: now + idx,
    content: String(emoji),
    uploaded_at: new Date(now + idx * 100).toISOString(),
    username: String(username || 'you'),
    agent_type: 'human',
    channel: 'main',
    parent_id: parentId,
  }));
  try {
    const raw = sessionStorage.getItem('ax_demo_payload');
    const arr = raw ? JSON.parse(raw) : [];
    const combined = [...arr, ...posts];
    sessionStorage.setItem('ax_demo_payload', JSON.stringify(combined));
  } catch {} // eslint-disable-line no-empty
  return posts;
}
