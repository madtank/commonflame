# MCP agent simulator

The simulator is a repeatable integration scenario using real local accounts,
workspace membership, sponsored OAuth, and MCP SDK clients. Actors are scripted;
no AI models or provider keys are involved. It tests collaboration, not model
quality or an always-on agent runtime.

## Contract

- Start with two actors; the normal scenario has five. A desired agent count can
  grow into the hundreds. Concurrency is independent of population size.
- Each actor owns a registration, grant and rotating refresh credential. Existing
  actors are reused when the same private state file is used again.
- Create clearly labeled synthetic humans and invite-only team workspaces.
  Exercise a second human joining through an actual workspace invitation.
- Cover named and canonical MCP URLs, pending human consent, approval, identity
  readback, task assignment/handoff, shared context, search, threaded messages, completion, credential
  refresh, and rejection of cross-workspace reads.
- Verify saved records through the API as both agent and human. A tool envelope
  without a persisted task/message is a failed scenario.
- Report counts, check outcomes and per-tool latency. Do not call population size
  a proven capacity limit; machine resources and rate limits still apply.
- Respect normal authorization and rate limits. Retry explicit HTTP 429 responses
  within a bounded deadline; never blindly replay mutations after transport errors.
- Keep credentials in a private, ignored state file with atomic checkpoints.
  Reports and console output contain no passwords, tokens, grants or invitations.
- Bind saved state to the installation's public origin. Refuse a mismatched origin,
  closed registration, and protected first-owner setup rather than bypassing policy.
- Existing accounts/workspaces are untouched. Fixtures remain for UI inspection;
  repeated scenarios create new task/message records in those fixtures.

## Run

Start the Compose stack, then:

```sh
python3 scripts/simulate.py --agents 2
python3 scripts/simulate.py --agents 5
python3 scripts/simulate.py --agents 100 --concurrency 5
```

Defaults: two synthetic humans, two team workspaces (one when starting with two
actors), two conversation rounds,
five concurrent actors, and private state/report files in `.local/simulator/`.
Increasing `--agents` reuses the same humans, workspaces and existing actors.
Changing topology requires a separate `--state` file; choose `--users` and
`--workspaces` at the start. Each team workspace has every simulated human as
an invited member; agent credentials remain bound to their approved workspace.
Personal home workspaces are additional to the requested team workspace count.

```sh
python3 scripts/simulate.py --agents 100 --users 4 --workspaces 4 \
  --concurrency 10 --rounds 3 --state .local/load/session.json \
  --report .local/load/report.json
```

Normal per-user workspace limits apply (at most five invite-only workspaces
created by a human). Signup is rate limited, so provisioning many humans can take
minutes. The deadline is configurable; hundreds of actors do not mean hundreds
of simultaneous requests. `--rounds` and `--pause` control activity duration.
The actors finish and become idle after a run; they do not listen autonomously.

For a separate installation, set its Compose project and public port in the usual
way, start it, and pass `--project <project>` to the simulator. No database reset
or existing data import is needed. The runner executes the installed MCP module
inside that project's MCP container; rebuild MCP after changing simulator code.

To inspect the fixtures in the interface, use one of the synthetic usernames
listed in the report. Its generated password is only in the private local state
file; inspect it privately, never paste that file into chat or an issue. This is
an operator test fixture, not a way to skip normal agent sponsorship.

The initial implementation uses the local public signup/device flow. Hosted
registration/first-owner protection must be configured by the operator separately;
this runner will not disable it. For recovery after interruption, keep the same
state file. Completed onboarding steps are checkpointed; an interrupted or failed run stops automatic resume. Inspect its report and
fixture records before explicitly using `--resume-after-failure`. Ambiguous
server writes can leave extra fixtures; the simulator does not silently replay
those writes. Do not delete state to silently retry a failed run.

## Acceptance evidence

Two-agent joining/conversation runs are part of Compose CI. Local validation
also covers five agents, adding actors without duplicate identities, multiple
sponsors/workspaces, and a larger population. The report records the actual tested
population and concurrency. Model-provider UAT and sustained Internet load are
separate work.
