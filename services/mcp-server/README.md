# Waystation MCP service

This resource server exposes seven tools: `whoami`, `messages`, `tasks`, `agents`, `spaces`, `context`, and `search`. It forwards operations to the backend API and serves the existing MCP Apps widgets. Experimental games stay disabled unless `AX_ENABLE_EXPERIMENTAL_GAMES=true`.

Start the whole application from the repository root with Docker Compose. The browser and MCP clients share the frontend origin, normally `http://localhost:3000`; connect to `http://localhost:3000/mcp`.

## Authentication contract

The backend is the OAuth authorization server. MCP validates backend-issued RS256 JWTs through JWKS and advertises the backend's public authorization URL. `/auth.md` is served by the backend. There is no Cognito proxy, AWS account, inline PAT, or separate token store in this service.

| Setting | Compose value | Purpose |
| --- | --- | --- |
| `API_URL` | `http://backend:8080` | Internal backend API |
| `AX_AUTH_MODE` | `remote` | Required; other values fail startup |
| `AX_AUTH_SERVER_URL` | `http://localhost:3000` | Public OAuth authorization-server origin |
| `AX_AUTH_SERVER_INTERNAL_URL` | `http://backend:8080` | Internal metadata fetch |
| `BACKEND_JWKS_URI` | `http://backend:8080/.well-known/jwks.json` | Token signature verification |
| `BACKEND_ISSUER` | `ax-backend` | Must match token `iss` exactly |
| `MCP_SERVER_URL` | `http://localhost:3000` | Public origin, without `/mcp` |
| `MCP_STATELESS_HTTP` | `true` | Independent HTTP requests |

The backend's MCP resource audience and MCP verifier must both use `http://localhost:3000/mcp`. When changing the public port or hostname, update both services together. Internal Docker hostnames must never appear in client-facing OAuth metadata.

Device authorization uses a named resource such as `http://localhost:3000/mcp/agents/my_agent` to provision and bind an agent. Issued tokens use the canonical `/mcp` audience; connect to `http://localhost:3000/mcp` after approval. Named MCP paths remain transport aliases for clients that use them, rather than distinct OAuth audiences.

Anonymous `tools/list` is supported for discovery. Initialize, tool calls, and sensitive resource reads return an OAuth challenge without a bearer token; invalid tokens fail before entering the tool stack. A token's `tools_allowed` restriction is honored and backend permissions still authorize each operation.

Stateless mode supports multiple Uvicorn workers and disables long-lived inbox notifications. The Docker command forces one worker if stateful mode is explicitly enabled.

## Tests and dependency baseline

Use Python 3.11:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Existing protocol, auth, permission, tool, and widget regressions were retained. Cloud deployment and Cognito-only tests were excluded with the removed code. FastMCP is pinned to the source's proven `3.3.1` baseline. `fakeredis==2.34.1` avoids the async API rename that breaks older FastMCP task storage. A FastMCP 4 upgrade is a separate follow-up, so consolidation can be tested independently.

The widgets currently load the pinned MCP Apps browser library from `unpkg.com`; those embedded widget surfaces require internet access. The API and MCP protocol runtime need no cloud account. Vendoring the browser library would make that part offline-capable.

Protocol keys and resource URIs carrying the old `ax` prefix remain compatibility identifiers. Visible server and widget names use Waystation.
