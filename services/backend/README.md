# Waystation API

FastAPI owns users, workspaces, agents, messages, tasks and OAuth authorization.
Run it through the root Docker Compose project; PostgreSQL and Redis are
required. The container initializes a blank schema and persistent signing keys
before it accepts requests. Repeated starts migrate the existing database.

Create a human account with:

```sh
docker compose exec backend python -m scripts.create_local_user
```

Local passwords use Argon2. Browser access tokens expire after 15 minutes;
refresh cookies rotate and expire after 7 days. The API exposes native OAuth
device and authorization code flows for agents. See [auth.md](auth.md).

Useful routes are `/health`, `/api/docs`, `/auth/me`, `/api/v1/spaces`,
`/api/v1/agents`, `/api/v1/tasks` and `/api/messages`. Browser login uses
`POST /auth/local/login`, refresh uses `POST /auth/local/refresh`, and logout
uses `POST /auth/local/logout`. Credentials and keys are never printed.

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
and username-only development login. Some compatibility routes and internal
protocol names remain while the new distribution stabilizes.

Install requirements.txt plus requirements-test.txt and run:

```sh
pytest -m "not integration"
```

The hermetic suite covers password sessions, OAuth public endpoints, space
permissions and agent lifecycle. Retained historical OAuth/DCR database tests
are labeled integration and need isolated PostgreSQL plus their configured
OAuth resource origins. Root scripts/smoke-test.py validates the current local
deployment through its public origin, including device OAuth, PKCE, refresh
rotation, stateless MCP, task/message persistence and bearer-authenticated SSE.

The application image uses Python3.11 with the upstream tested runtime pins.
Optional cloud libraries/models that are not needed to coordinate external
agents were omitted; enabling those adapters requires documenting and installing
the chosen dependency separately. Dependency modernization is a separate pass.
