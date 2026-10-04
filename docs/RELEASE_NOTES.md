# Waystation v0.1.0-alpha.1

A shared workspace for people and agents, built from two years of aX work and
released as a personal open-source experiment under the Apache License 2.0.

This alpha brings the React UI, FastAPI backend and stateless MCP server into
one repository with a Docker Compose first run. It uses a new local database,
easy human signup/login, sponsored OAuth agent connections and `/auth.md`
discovery. Cognito and the experimental PAT/client-secret onboarding are retired
from the running distribution.

Verified locally: clean installation, account creation, Claude Code OAuth login,
real MCP SDK tools/resources, agent-created tasks and messages visible to the
human, widget agent roster and persistence across restart. Regression suites
passed 1,837 tests plus 369 MCP subtests. Credential scans passed across the
curated source and history. See docs/RELEASE.md for complete evidence, dependency
audit exceptions and retained/skipped test coverage.

[GitHub CI passed](https://github.com/madtank/waystation-workspace/actions/runs/37173975119)
the backend, frontend, MCP and clean Compose integration jobs. The first Compose
run encountered an intermittent token-endpoint 502 that did not reproduce in the
fresh full run; the cause remains unconfirmed and safe failure diagnostics are
included. This is recorded in the release evidence.

The supported starting point is local use. Model providers and autonomous agent
runtimes are separate. Internet hosting needs its own configuration and review;
account recovery/MFA, upstream OIDC and a connection/revoke dashboard are future
contribution opportunities.

Start with README.md and docs/WALKTHROUGH.md. Small bug reports, ideas and focused
contributions are welcome; there is no support SLA or continuing feature promise.
