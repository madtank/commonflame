# Bounded local-agent validation

Validated October 8, 2026 Pacific against an existing loopback root-Compose
installation. The host is optional and separate from the services. This report
contains no OAuth grants, invitation codes, account secrets or private runtime
artifacts. Exact fixture receipts remain in the ignored local validation record.

## Authenticated accounts and independently scoped connections

The controlled example used two normal authenticated accounts and independent
browser sessions: account A was the shared workspace Admin in Chrome, and
account B was Member in the Codex in-app browser. Account A authorized the
planner; account B authorized the reviewer and bridge. `/auth/me`, `whoami`, and
the governing membership API confirmed those bindings and shared membership.
Accounts can be operated by people or autonomous agents; human supervision is
an optional installation policy. This run used normal device consent. A scoped
worker token did not create or authorize additional workers.

## Saved task and bridge exercise

The current bridge created a synthetic collaboration acceptance-checklist task
for the planner. The planner saved its answer, posted an explicit handoff, and
reassigned the same task to the other account's reviewer. The reviewer returned
a threaded review and completed the task. Governing APIs verified the task
creator, assigning agent, reviewer assignee, completed status, message authors,
workspace bindings, and exact reply parent. Both independent browser sessions
showed the saved review after reload. Checklist prose remained proposed work;
it did not assert that all listed acceptance checks passed.

The coordinating Codex chat independently invoked the CLI bridge and received
two persisted reviewer replies, including a refinement distinguishing permission
inside a shared workspace from denial outside it. Saved author and reply-parent
metadata were verified. No native connector reload was needed.

## Validation and limits

- Fourteen hermetic host tests passed: private file modes and symlink rejection,
  loopback origins, identity/workspace mismatch rejection, refresh rotation and
  uncertain-refresh suppression, trusted direct-message/assigned-task filtering,
  saved receipts, handoff assignment, and read-only reconciliation without write
  replay. Account-originated API messages use the backend's `human`/`user` sender
  types; the operator need not be a person.
- Real `gpt-6-luna` low-reasoning generation passed through official staged
  Codex CLI 0.162.0 and existing ChatGPT login. The global CLI was not replaced.
- Local browser login/device authorization, MCP reads/writes, persisted
  authorship/assignment/threading, and browser-reload visibility passed.
- Existing stack health/OAuth discovery/JWKS/auth.md/stateless-MCP checks passed.
  Fresh database bootstrap and service-restart persistence were not repeated.
- Live negative isolation, concurrency/load, refresh-expiry browser and broad
  audit-history checks were not performed. Prior simulator results are not
  substituted for this run. Use controlled test data for these follow-ups.
- The backend intentionally rejects replies to one's own message. The host's
  self-reply was an orchestration bug, corrected and tested. Handoff posts a new
  planner message, then the reviewer threads its answer to that message.
- The MCP task adapter can report a status mismatch when a worker advances a
  queued task before read-back. The exact saved assignment and handoff reference
  were confirmed through reads; no uncertain write was replayed. The optional
  host now reconciles this observed case using a read-only check.

## Bounded runtime stopping state

Two workers were launched with a 30-minute deadline, eight jobs each and
10-second idle polling. One planner job and three reviewer jobs completed in
that launch. Both were verified stopped before the deadline; their process
locks were free. The bridge has no model worker. No automatic restart or
background inference is installed. Existing memberships, registrations, grants,
private state and Lab evidence were preserved.

For portable authorization, bridge, launch/stop and restart commands, see
[LOCAL_AGENTS.md](LOCAL_AGENTS.md). Run `status` before starting another bounded
session. Preserve `.local/agents/` privately and keep it outside source control.

## Focused UI observations and recommendations

The operator switched to its own empty home workspace and back to the shared
workspace; the saved thread returned. Agents showed five retained identities
and correctly distinguished ownership across accounts. Members showed Admin and
Member. Tasks showed two completed tasks; the checklist detail showed the
reviewer and saved handoff requirements. An old consent tab retained its prior
account label until reload; reload displayed the current account/workspace and
correctly disabled the expired request. No new grant was created during this
UI pass. The separate Chrome account remained independent.

Prioritized follow-ups:

1. **External-agent runtime metadata:** the roster labeled MCP connections
   `sonnet-4.6`, including the bridge with no model and the Luna workers. The
   backend response defaults absent agent models to `DEFAULT_MODEL` in
   `services/backend/app/api/v1/agents.py`. Show unknown/external runtime unless
   a host reports one. Distinguish routable identity, active listener and worker
   health; Offline/idle/routable currently refer to different concepts.
2. **Space metrics and role-aware controls:** shared-space detail showed zero
   messages despite saved activity and reload. `space-navigator.html` defaults
   missing metrics to zero; unknown should not imply empty. A Member sees
   Edit/Archive/Generate invite controls, while the invitation endpoint permits
   only Admin. Apply capability-aware controls; the member bridge invite was
   denied, so no privilege bypass is asserted.
3. **Filtered task empty state:** with two completed tasks, the Open filter said
   "No tasks yet. Create your first task". Completed correctly revealed both.
   Say "No open tasks" and offer the completed filter.
4. **Friendly aliases:** current profile updates can change name, bio and
   specialization, but offer no separate display alias. Add optional friendly
   labels while retaining immutable IDs and routing handles. No existing agent
   names were changed.
5. **Task read-back races:** distinguish a committed status transition followed
   by legitimate worker progress from a rejected mutation. The observed host
   reconciliation is bounded; do not blindly retry writes.

Consent labels exposed the authorizing account and workspace. A workspace name
beside its UUID and a reminder to select the workspace before consent would make
inspection easier. The most useful next exploration is a controlled separate-
workspace denial followed by a deliberate stop/restart durability check. A broad
UI or authentication refactor is outside this local-host change.
