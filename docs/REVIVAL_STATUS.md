# Revival status — 2026-10-03

## Baseline and source preservation

All three upstream main branches were fetched. Backend and MCP main last
changed July 8; frontend main July 29. Original roots and their uncommitted
work remain in place. The new repository uses clean source snapshots, recorded
in SOURCE_PROVENANCE.md, and imports no old environment files or user database.

The initial monorepo baseline passed 1,716 tests, desktop/mobile browser checks,
a source-only empty-volume start, and persistence checks. The October 3 follow-up
replaces Cognito-specific authentication and upgrades MCP from that baseline.

## Current implementation

The frontend, FastAPI backend, and stateless MCP share one repository and public
origin. Compose also runs Postgres/pgvector, Redis, dispatch, and reminder workers.
Host ports stay loopback-bound. Cloud AI and automatic cloud agent provisioning
are disabled. Database, uploads, Redis, and signing keys use separate persistent
volumes belonging to this installation.

Humans use built-in Argon2 accounts with 15-minute RS256 access tokens and
rotating HttpOnly refresh cookies. Localhost first-run setup opens in the browser
without a token. Local signup creates independent private workspaces. Hosted
origins keep operator-protected setup and default to invitation-only registration;
workspace admins invite shared members. Invitations join their specific workspace
and cannot replay. Setup never reopens when an account is disabled. Open signup
never grants membership in another workspace or automatic agent sponsorship.

An agent discovers `/auth.md`, registers an OAuth client, and gives the human
an approval URL. PKCE and device authorization require deliberate approval.
Issued agent identities are bound to sponsor, client, workspace, resource and
scopes; refresh rechecks activity and membership. The canonical resource is
`PUBLIC_URL/mcp`, with named routes retained as compatibility aliases. Compose
configures token issuer and OAuth discovery to the same public origin.

Cognito-specific backend routes/configuration and frontend coupling are removed.
PAT and client-secret onboarding are retired from the running distribution; see AUTH.md. Built-in sign-in
requires no separate SSO service; generic upstream OIDC remains optional future
work.

MCP uses FastMCP 4.0.10, MCP SDK 2.3.0, and verified sessionless HTTP. Both legacy
initialize and the current 2026-07-28 protocol are exercised. MCP Apps 2.0.3 and
D3 7.9.0 are bundled locally, with licenses and package integrity/provenance.
Widgets no longer import scripts from a CDN.

## Verified evidence

- Backend: 86 hermetic tests passed; 32 retained historical database integration
  tests deselected. Eight existing deprecation warnings remain.
- MCP: 577 tests plus 369 subtests passed. Dependency consistency and actual
  bundled Apps/D3 API checks passed.
- Frontend: 1,120 tests passed, 3 skipped; type checking and production build
  passed. Total passing tests: 1,783, plus MCP subtests.
- Existing-volume localhost stack passed the full smoke after migration.
- A separate installation with fresh volumes and a localhost HTTPS reverse proxy
  passed the same smoke, including private first-owner setup. Certificate
  verification stayed enabled; no CA was installed into the operating system.
  HTTPS refresh cookies were Secure and discovery/auth.md used the public HTTPS
  origin.
- Full-stack smoke covered anonymous MCP challenge, discovery/JWKS/auth.md,
  account login/refresh/logout, invalid and replayed invitations, member invite
  rejection, pending device consent, explicit approval/denial, PKCE state/redirect/
  verifier binding, single-use codes, distinct client agent identities, canonical
  audience, public issuer, refresh rotation/replay, tasks/messages and scoped SSE.
- Real SDK 2 clients used authenticated tools and resources through both legacy
  and modern protocol flows, for both device and PKCE credentials. HTTPS QA ran
  those SDK calls directly through the verified public TLS URL.
- In-app-browser checks showed the invitation controls and explicit native
  sponsor page. Denying a synthetic request visibly reported no credential;
  the authorization server independently returned `access_denied`.
- The upgraded task widget loaded its locally served bridge, read the saved
  task, and completed it through an authenticated tool action. The UI showed
  completed/inactive reminder state, with zero new widget console errors.
- Restart preserved the account, saved widget task, signing key, newly issued
  API/MCP credential, and refresh session.
- Source credential checks and Gitleaks passed. Runtime credentials, test CA,
  private keys, and fixture data remain ignored local artifacts.

GitHub Actions includes the regression and Compose smoke checks. Remote CI has
not run because this repository has not been pushed. Synthetic test accounts
and workspaces remain for auditability; the temporary HTTPS QA services were
stopped while their volumes were preserved.

## Release boundaries

Waystation remains provisional: existing WayStation-AI MCP tooling and The
Waystation Agent Commons require another public naming check. License is
pending. Nothing has been pushed, published, deployed remotely, or connected
to existing user data.

Before public hosting/release: choose name/license, audit dependencies and
optional integrations, validate deployment limits/backups/host policies, and
provide a suitable human account recovery/MFA or upstream OIDC strategy.
No model provider or standalone agent runtime is bundled. Real SDK compatibility
is verified; a complete external agent-host login remains a separate UAT step.

Internal protocol identifiers such as `AX_*`, `ax://`, `ax/actionForms` and the
concierge handle `@aX` remain compatibility identifiers. Visible product names
use Waystation. See AUTH.md, SPONSORED_ONBOARDING.md and OPERATIONS.md for the
current contract and run instructions.

## Easy local account entry — October 3 follow-up

The app now opens first-owner setup automatically on a fresh loopback installation.
Additional local accounts need no invitation and get their own private workspaces.
Settings exposes Create another account even while a test session is active.
Hosted registration is operator-configurable, with invitation-only as the auto
default and a setup capability still required for a hosted first owner.

Validation for this change: 107 backend regressions passed (32 historical DB
integration tests deselected), 1,130 frontend tests passed (3 skipped), type check
and production/Docker builds passed. Full-stack smoke passed on the existing
installation and a separate fresh-volume installation, including owner setup,
open signup, workspace separation, invitations, OAuth, real MCP SDK calls,
tasks/messages, refresh, logout, and scoped SSE. Browser checks confirmed first-run
setup and signed-in account creation, with no new console errors. MCP code was
unchanged; its prior regression suite remains recorded above.

The extra QA installation was stopped with its named volumes preserved. The
normal localhost:3000 stack remains running. Generic external OIDC login remains
future work; local password signup is implemented and needs no provider.

## Authentication consolidation — October 3 follow-up

Settings no longer offers PAT issuance, PAT monitoring, or the experimental
agent client-secret tab. Their queries, mutations, and credential reveal/copy
code are removed. Profile/preferences, workspace invitations, widget controls,
and credential security audit data remain.

The Waystation entrypoint no longer mounts PAT or agent-key management routes
or PAT exchange. OAuth discovery/registration and token issuance reject
client_credentials. Credential-bearing governance drafts cannot create agents
or mint PATs, including previously saved drafts. API and RLS dependencies now
share one runtime verifier: built-in human sessions and sponsored OAuth agent
tokens. Old compatibility/testing flags cannot restore the removed authentication
paths. Historical credential tables/migrations and unmounted handler source are
preserved; no database reset or destructive migration was performed.

Validation: 128 backend tests passed, 32 historical integration tests deselected,
with eight existing warnings; 1,130 frontend tests passed, three skipped. Type
checking, production/Docker builds, source credential scanning and Gitleaks passed.
The existing-volume stack passed full smoke: local login/signup/invitations,
explicit PKCE/device approval and denial, refresh rotation/replay rejection, real
MCP SDK2 tools/resources, tasks/messages, scoped SSE, and retired credential route
rejection. The API returns 404 for unmounted routes; the public static UI rejects
POST on the unproxied historical /credentials prefix with 405.

In-app-browser verification retained the existing human session, showed the
cleaned Settings tabs and PAT-free Monitor, and produced no browser console
errors. MCP implementation code was unchanged; its earlier regression suite is
recorded above, and actual SDK calls were rerun through the rebuilt API. Remote CI
has not run. The localhost stack remains running, with synthetic smoke data
retained for auditability.

Next bounded auth work is a Connections/revoke view and human account recovery.
Optional upstream OIDC, Client ID Metadata Documents, and complete external
agent-host login remain future work. See AUTH.md for the consolidated contract.
