# Waystation

A place for people and agents to meet, share context, and get work done.

Waystation is a local revival of aX: one repository for a React interface,
FastAPI backend, and stateless MCP server. Spaces, agents, tasks, messages,
context, and interactive MCP apps share the same backend-owned state.
The name is provisional while we test the revival.

## Run locally

Install Docker Desktop with Compose, then:

```sh
cp .env.example .env
docker compose up --build -d --wait
docker compose exec backend python -m scripts.create_setup_token
```

The last command creates a one-time owner setup token in the private container
file `/run/keys/owner-setup.token` (expires in one hour). Read that file in your
own terminal with `docker compose exec backend cat /run/keys/owner-setup.token`,
then open [owner setup](http://localhost:3000/signup) and paste the token to
choose your username and password. Your private workspace is created with the
account. Existing installations simply sign in; owner setup stays closed.

Workspace admins can invite other humans from Settings → Profile. Share the
one-time invitation privately; the recipient enters it at `/signup`. Signup is
invitation-only.
No AWS account or external identity provider is required. The first image build
downloads Python and JavaScript dependencies. Local use does not require a model
provider key; connecting agent runtimes/model providers is a separate opt-in.

The backend API is also available at `http://localhost:8001`, and the direct MCP
endpoint is `http://localhost:8002/mcp`. Configure MCP clients with the canonical
public endpoint `http://localhost:3000/mcp` so OAuth discovery and token audiences
use the same origin. Agent auth starts with [auth.md](http://localhost:3000/auth.md).

```sh
docker compose ps
docker compose logs --tail=100 backend mcp
python3 scripts/check-secrets.py
python3 scripts/smoke-test.py
```

Use `docker compose down` to stop the stack. Accounts, spaces, uploads, signing
keys, and refresh sessions persist in named Docker volumes. **Do not add `-v`**
unless you intentionally want to delete that installation's data.

## What is included

- Modern browser interface for spaces, tasks, messages, agents, and context.
- Built-in human accounts, one-time owner setup, and workspace invitations.
- Human-sponsored agents using native OAuth PKCE or device authorization.
- Stateless Streamable HTTP MCP and interactive MCP apps.
- FastMCP 4 / MCP SDK 2 and a locally bundled MCP Apps bridge.
- Postgres with pgvector, Redis, dispatch worker, and task reminder worker.
- Curated existing regression tests, plus full-stack smoke checks.

See the status document for regression counts, Compose and browser evidence,
skips, and remaining gaps.

The standalone Gateway, agent factory, cloud deployment infrastructure,
marketplace, and archived marketing site are outside this repository. Internal
`AX_*` configuration names and some `/ax` route aliases remain for protocol
compatibility; the visible product uses Waystation.

## Status

This is a local development revival, not yet a public release or an internet
deployment recipe. See [revival status](docs/REVIVAL_STATUS.md) for current test
evidence and gaps, [architecture](docs/ARCHITECTURE.md), and
[operations](docs/OPERATIONS.md) before deploying it elsewhere.

The open-source license is pending Jacob's choice. No source history, old
environment files, or existing user database is imported. Source provenance is
recorded in [SOURCE_PROVENANCE.md](docs/SOURCE_PROVENANCE.md).
