# Revival status — 2026-10-02

## Baseline inspection

- Fetched all three upstream repositories successfully.
- Backend and MCP main last changed July 8; frontend main July 29.
- Original backend/MCP roots contain uncommitted work, preserved in place.
- Original frontend is clean but on an older feature branch.
- New source snapshots are detached, clean, and based on fetched main.
- MCP already defaults to stateless HTTP; a protocol rewrite is unnecessary.
- Backend has OAuth discovery, JWKS, and native device authorization.
- Browser authentication is Cognito-specific and requires a standalone local
  account path for a cloud-free quickstart.
- The supplied analysis incorrectly says `/health` is missing: current backend
  source has both `/health` and versioned health routes.

## Local build verified

The curated monorepo runs the frontend, backend, stateless MCP server, Postgres,
Redis, dispatch worker, and reminders worker with one Compose command. Host
ports bind to loopback; new volumes keep this installation separate from the
old aX stack. Cloud-only startup defaults, old environment files, credentials,
marketing archives, and dead legacy entrypoints are excluded.

Browser accounts use explicit usernames/passwords, Argon2 password hashing,
short-lived RS256 access tokens, and rotating HttpOnly refresh cookies. Agent
connections use the backend's native OAuth device or PKCE consent flow. Signing
keys persist in a private Docker volume. Cloud AI execution and automatic
managed-agent provisioning are disabled by default.

Validation against the final local build:

- Backend: 63 hermetic tests passed; 33 retained database integration tests
  were deselected. The real Compose smoke covers PostgreSQL-backed auth and
  task/message persistence.
- MCP: 568 regression checks passed.
- Frontend: 1,085 tests passed, 3 skipped; type checking and Node 24 production
  build passed.
- Full-stack smoke passed: discovery/JWKS/auth.md, every anonymous tool-call
  challenge, browser login/refresh/logout, device consent, OAuth refresh
  rotation/replay rejection, PKCE redirect/state/verifier binding and one-time
  codes, authenticated MCP, saved tasks/messages, and workspace-scoped SSE.
- Desktop and mobile browser checks passed, including settings, two-tab refresh
  coordination, session restore, and shared logout, with no page errors or
  failed authenticated API requests. Visible in-app-browser UAT created a task
  through the real MCP app and opened its saved detail.
- Fixed legacy theme broadcasts that the modern MCP Apps bridge rejected;
  widget theme changes now use direct legacy setters and valid MCP host-context
  notifications, with regression coverage.
- A source-only copy built and started all seven services with an empty database
  and isolated volumes on different ports, then passed the same full smoke.
- Restarting the backend, MCP, and workers preserved the browser account, the
  task created in the visible widget, the public signing key, the refresh
  session, and already-issued authenticated API/MCP credentials.
- Offline credential-pattern scanning and Gitleaks passed on the curated source.

GitHub Actions includes the regression and Compose checks. Remote CI has not
run because the repository has not been pushed.

## Release decisions

Waystation is a provisional local name. A live name check found existing
WayStation-AI MCP tooling and The Waystation Agent Commons, so public naming
needs another pass. License is pending. Nothing has been pushed, published,
deployed remotely, or connected to existing user data.

The local account-creation command is documented in the root README. Test
accounts and their workspaces are synthetic and remain in the isolated local
database; no old user database was imported.

## Remaining release work

- Choose the final public name and license.
- Audit dependencies and review optional integrations before public release.
- Vendor the MCP Apps browser bridge for fully offline widgets.
- Connect and validate a real external agent runtime; a model provider/runtime
  is not bundled into the default stack.
- Review HTTPS, deployment secrets, limits, backup/restore, and operational
  controls before exposing an instance beyond localhost.

## MCP baseline and known limits

The curated MCP tree keeps 42 active runtime files and 32 focused Python test
files. The isolated Python 3.11 regression suite passes 568 checks, including
actual RSA/JWKS validation of signature, issuer, audience, and expiry. Browser
JWTs retain user authorship even on a named agent route. The service pins the
existing FastMCP 3.3.1 baseline; upgrading to FastMCP 4 is a separate follow-up.

Embedded MCP Apps currently import a pinned browser bridge from `unpkg.com`.
Those widget surfaces require internet access until the bridge is vendored.
The backend API and MCP protocol runtime do not require an AWS account.

User-visible MCP names and text now use Waystation. Old protocol identifiers
such as `ax://`, `ax/actionForms`, and the concierge routing handle `@aX` remain
for compatibility.
