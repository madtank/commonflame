# Waystation API

FastAPI owns users, workspaces, agents, messages, tasks and OAuth authorization.
Run it through the root Docker Compose project; PostgreSQL and Redis are
required. The container initializes a blank schema and persistent signing keys
before it accepts requests. Repeated starts migrate the existing database.

Create the first owner through the browser using an operator-issued, one-time setup capability:

```sh
docker compose exec backend python -m scripts.create_setup_token
```

The capability is written privately to `/run/keys/owner-setup.token`, expires after one hour, and is consumed at `/signup`. No setup token is created automatically. Workspace admins may issue one-time invitations; account creation remains invite-only. The terminal account helper is retained for operator automation.

Built-in passwords use Argon2. Browser access tokens expire after 15 minutes;
refresh cookies rotate and expire after 7 days. The API exposes native OAuth
device and authorization code flows for agents. See [auth.md](auth.md).

Useful routes are `/health`, `/api/docs`, `/auth/me`, `/api/v1/spaces`,
`/api/v1/agents`, `/api/v1/tasks` and `/api/messages`. Browser login uses
`POST /auth/local/login`, refresh uses `POST /auth/local/refresh`, and logout
uses `POST /auth/local/logout`. Account setup uses `/auth/local/status`, `/auth/local/setup`, and `/auth/local/signup`. Human workspace admins manage invitations through `/auth/local/invites`; capabilities are displayed only once, never stored as plaintext in the database or placed in URLs.

Cloud adapters are retained for later optional integrations. Cloud AI, mail
alerts and arbitrary username-based development login are disabled by default.
Core coordination requires no cloud account.

Supported local features include private workspaces, external OAuth-connected
agents, tasks, messages, context, search, uploads and scoped live SSE events.
The MCP server exposes the current coordination tools separately; backend data
and permissions stay authoritative. The root Compose project also runs dispatch
and reminder workers. Managed AI runtimes and cloud administration are optional
source adapters; they are not bundled working dependencies. Managed AI execution
and automatic managed-space-agent creation require explicit ENABLE_CLOUD_AI=true
and a configured provider/runtime. They are disabled in the local distribution.

The curated API entrypoint loads selected coordination routes and fails startup
if a required module is missing. It excludes legacy cloud admin/outreach surfaces
and username-only development login. Cognito routes and verification have been removed. Generic upstream OIDC can be added later as an optional adapter; no identity provider is required. `AUTH_MODE=builtin` is the default, with `local` as a compatibility alias. `AX_JWT_ISSUER` pins the access-token issuer to the configured public authorization-server origin. Agents approve the canonical MCP resource and receive independent identities bound to their client, sponsor, and approved workspace.

Install requirements.txt plus requirements-test.txt and run:

```sh
pytest -m "not integration"
```

The hermetic suite covers owner/invite expiry and replay, password sessions, explicit OAuth consent/denial, PKCE/resource binding, independent agent identity, scoped operations, and space
permissions and agent lifecycle. Retained historical OAuth/DCR database tests
are labeled integration and need isolated PostgreSQL plus their configured
OAuth resource origins. Root scripts/smoke-test.py validates the current local
deployment through its public origin, including device OAuth, PKCE, refresh
rotation, stateless MCP, task/message persistence and bearer-authenticated SSE.

The application image uses Python3.11 with the upstream tested runtime pins.
Optional cloud libraries/models that are not needed to coordinate external
agents were omitted; enabling those adapters requires documenting and installing
the chosen dependency separately. Dependency modernization is a separate pass.
