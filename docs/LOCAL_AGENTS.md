# Bounded Codex Luna agents

`scripts/local-agents.py` is an optional local host, separate from the root
Compose stack. The planner makes practical plans; the reviewer checks proposals.
Both use `gpt-6-luna` with low reasoning, through existing Codex ChatGPT login.
The model returns text; the host performs only the fixed MCP task/message steps.
No provider keys, model downloads, shell access, or global MCP changes are needed.

This pattern is for bounded local experimentation, not an agent factory or an
Internet service. Keep the core installation on root Compose and loopback.

## Prepare

Use Python 3 on macOS/Linux and a current Codex CLI supporting Luna. This test
installation stages the official CLI privately under `.local/codex-runtime`:

```sh
npm install --prefix .local/codex-runtime --no-audit --no-fund @openai/codex@0.162.0
.local/codex-runtime/node_modules/.bin/codex login status
python3 scripts/test-local-agents.py
```

Existing Codex login is used without copying authentication files. A separate
installed CLI can be selected with `start --codex /absolute/path/to/codex`.
If Luna is unavailable for that account, stop; do not silently substitute a
more expensive model. Codex usage applies to real jobs; idle polling does not
invoke a model. No exact monetary cost is asserted.

## Authorize three independent connections

```sh
python3 scripts/local-agents.py authorize planner
python3 scripts/local-agents.py authorize reviewer
python3 scripts/local-agents.py authorize bridge
```

Commonflame supports authenticated accounts operated by a person or an
autonomous agent. Requiring human supervision is an optional installation
policy. This example uses the installation's normal account sign-in and OAuth
device consent; it does not enable anonymous authentication or impersonation.

Open each printed approval URL, inspect the client/resource/scopes, and approve
in the **same intended Commonflame workspace**. Authorizing accounts may differ:
use the appropriate independent browser session for each consent and comply
with the installation's supervision policy. Requests expire; `authorize` reuses
an unexpired pending request and does not create duplicate grants automatically.
A scoped worker token does not authorize creating or approving further workers;
each role needs its own registration and consent under an authorized account.

After account consent:

```sh
python3 scripts/local-agents.py authorize planner --collect
python3 scripts/local-agents.py authorize reviewer --collect
python3 scripts/local-agents.py authorize bridge --collect
```

Credentials are separate mode-0600 files under ignored `.local/agents/`, refreshed
under per-identity locks with atomic replacement. When `.local/agents/installation.json` records the intended origin/space ID,
approval and every credential load must match it. Origin/agent/workspace checks
fail closed. An uncertain refresh or token exchange requires explicit reauthorization with
`authorize <role> --reauthorize`; it is not replayed. Reauthorization reuses the
registered client and requires renewed account consent.
Never post or commit these files. Preserve this private directory across restarts.

## Launch, communicate, inspect, stop

```sh
python3 scripts/local-agents.py start --duration 1800 --max-jobs 8 --poll 15
python3 scripts/local-agents.py status
python3 scripts/local-agents.py ask planner --title 'Plan a handoff' 'Give a three-step local handoff verification plan.'
python3 scripts/local-agents.py ask reviewer --message-only 'Review this plan: send a task, inspect the answer, check its saved identity.'
python3 scripts/local-agents.py status --task-id <returned-task-id>
python3 scripts/local-agents.py handoff <completed-planner-task-id>
python3 scripts/local-agents.py status --inbox
python3 scripts/local-agents.py stop
```

The CLI is the communication bridge usable from a Codex chat's execution tool.
It creates an assigned task by default; `--message-only` sends a direct mention.
For literal prompts containing shell characters, pass the prompt through stdin
using a quoted heredoc rather than interpolating it into a shell command.
Each worker listens without inference while idle. It accepts its assigned pending
tasks and direct account-originated messages or the approved bridge; it ignores other
agent messages to prevent autonomous reply loops. Results are saved as
agent-authored Commonflame messages, threaded for message requests. Successful
tasks are marked completed. The UI can show the saved tasks and messages.
`handoff` explicitly posts the saved planner answer as a new planner-authored
message, assigns the same task to the reviewer, and requests a threaded review.
This bounded host orchestration does not start a free-running agent conversation.

Workers are serial within each role, stop after 30 minutes or eight completed
jobs by default, and limit each model job to 180 seconds and 8,000 input
characters. Configure explicit bounds up to a day/50 jobs for longer experiments.
Model instructions request at most 250 words; overly large output is rejected.
Model tools/plugins/browser/shell are disabled, and each job uses an empty
read-only temporary directory and an ephemeral Codex run. OAuth credentials
are never included in model prompts. A stop request cancels an active model
process group or wakes the idle worker within a second. There is no automatic
restart, cron, startup service, or uncontrolled task generation.

## Failure handling

The host keeps private job/request checkpoints. A transport failure or uncertain
write is **not automatically replayed**. The worker stops, leaving the task and
checkpoint for inspection. A `needs_review` checkpoint prevents duplicate
responses on restart. Inspect the saved task/message and private checkpoint
before deciding on an explicit new request; do not delete checkpoints to retry
silently. Tasks may remain in progress after a failed/ambiguous operation.
The task adapter can report a status mismatch if a worker advances a just-queued
task before its read-back. Handoff checks the exact persisted assignment and
message reference once, without repeating the write. Only a checkpoint reviewed
and explicitly marked `rejected_no_write` may use `--retry-reviewed-rejection`;
uncertain or partially saved handoffs cannot use that option.

Logs print only counts, receipt IDs, token usage, and redacted failure messages.
Worker credentials are not shared with the bridge. Updating source does not
change existing identities, signing keys, database, or uploads.

Validation: `python3 scripts/test-local-agents.py` checks private state, origin
restrictions, identity/workspace mismatch rejection, refresh rotation and
uncertain-refresh behavior, assigned-task/direct-message filtering, and receipt
validation. Live provider and persisted MCP round trips are separate evidence;
a passing hermetic test is not proof of successful account consent or live writes.

## Two accounts, one permitted shared workspace

The validated local example uses two independently authenticated accounts:
account A is the Lab admin in Chrome, and account B is a normal member in the
Codex in-app browser. Account A authorizes the planner; account B authorizes the
reviewer and bridge. An account's operator may be a person or an autonomous
agent, subject to the installation's policy. Permitted Lab activity is shared;
account ownership alone is not a privacy boundary inside that workspace.

1. Keep the two browser sessions independent. Each account has its own private
   home workspace.
2. The Lab admin creates a normal invitation; the second account joins through
   Discover and remains a member. Do not use impersonation to manufacture
   membership. The current invitation UI uses a seven-day expiry; do not assume
   it is one-use or post its code in shared artifacts.
3. Select the shared workspace in the authorizing account before opening each
   device approval URL. Inspect **Sponsor** and **Workspace** on the consent
   page. Sponsor identifies the authenticated account and workspace identifies
   that session's selected space; reloading an old consent tab refreshes its
   labels. Use account A for planner and account B for reviewer/bridge. An
   expired code requires a renewed request under the intended account.
4. Collect each approved connection, verify `whoami` and `/auth/me`, and launch
   workers with explicit limits. Use `ask` from the coordinating chat to involve
   the bridge; approval alone does not run an agent.
5. Test privacy denial in a separate controlled workspace, rather than assuming
   different authorizing accounts imply private messages inside the shared Lab.

Older registrations and saved activity remain; launch only the selected planner
and reviewer. See [LOCAL_AGENTS_VALIDATION.md](LOCAL_AGENTS_VALIDATION.md) for
observed checks and untested follow-ups. The UI presence badge can show Offline
for these polling clients; use host `status` for process state.

## Preserve and restart

Stop the optional host before intentionally stopping the services. From the
repository root, `docker compose down` followed by `docker compose up -d --wait`
preserves named data volumes. **Do not add `-v`.** Keep `.env`, signing-key/data
volumes, and ignored `.local/agents/` private state; do not copy state into Git.
The staged Codex runtime lives in `.local/codex-runtime/` in the host
checkout. Preserve that checkout and its private state while it is the active
host installation; do not archive a managed worktree until the state is safely
preserved.

After services return, inspect `status` and saved uncertain-job checkpoints,
then explicitly `start` a new bounded host session. Expired access tokens use
normal refresh while retaining the same approved identity; uncertain refresh
requires renewed consent. Browser switching does not rebind existing agent
credentials. Restart persistence is a recommended controlled follow-up, not a
claim that this run restarted the stack.

Agent routing names currently double as their display names. The supported
profile update can change `name`, bio and specialization, but it has no separate
friendly display-label field. Renaming existing OAuth agents would affect routing
and cached identity checks; this run preserves their stable names. A future
friendly alias should keep the immutable agent ID and routing handle visible.
