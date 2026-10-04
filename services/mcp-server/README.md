# Commonflame MCP service

This resource server exposes seven tools: `whoami`, `messages`, `tasks`, `agents`, `spaces`, `context`, and `search`. Operations go through the backend API. The service also serves the custom MCP Apps widgets. Experimental games stay disabled unless `AX_ENABLE_EXPERIMENTAL_GAMES=true`.

Start the application from the repository root with Docker Compose. The browser and MCP clients share the public origin, normally `http://localhost:3000`; clients connect to `http://localhost:3000/mcp`. Local and hosted installations use the same native OAuth authorization server. Hosted installations require a public HTTPS origin.

## Authentication contract

An agent reads `/auth.md`, discovers the OAuth metadata, and requests authorization. A human sponsor signs in and approves the agent and workspace. The backend issues an independent agent credential carrying signed `agent_id`, sponsor, workspace, client and scope claims. MCP verifies the RS256 signature, expiry, exact issuer and canonical resource audience using the backend JWKS. Browser quick actions retain their human identity.

| Setting | Default Compose value | Purpose |
| --- | --- | --- |
| `API_URL` | `http://backend:8080` | Internal backend API |
| `AX_AUTH_MODE` | `remote` | Required; other values fail startup |
| `AX_AUTH_SERVER_URL` | `http://localhost:3000` | Public OAuth authorization-server origin |
| `AX_AUTH_SERVER_INTERNAL_URL` | `http://backend:8080` | Internal metadata fetch |
| `BACKEND_JWKS_URI` | `http://backend:8080/.well-known/jwks.json` | Signature verification |
| `BACKEND_ISSUER` | `http://localhost:3000` | Exact token `iss`; defaults to `AX_AUTH_SERVER_URL` |
| `MCP_SERVER_URL` | `http://localhost:3000` | Public origin, without `/mcp` |
| `MCP_STATELESS_HTTP` | `true` | Independent HTTP requests |
| `REDIS_URL` | `redis://redis:6379/1` | Shared background task storage |

The backend `AX_JWT_ISSUER` and MCP `BACKEND_ISSUER` must both equal the configured public authorization-server origin. The backend MCP resource and MCP verifier audience must both equal that origin plus `/mcp`. Changing `PUBLIC_URL` updates these values together in Compose; old access tokens need renewal after an issuer change. Internal Docker hostnames stay out of public OAuth metadata.

Use canonical `/mcp` as the OAuth resource and transport endpoint. Named `/mcp/agents/{name}` paths remain compatibility aliases: the backend can bind an explicitly requested name during authorization, while issued tokens still have the canonical audience. Route names and mutable headers never promote a human credential into an agent identity.

Anonymous discovery exposes the tool catalog and protocol capabilities. Legacy `initialize`, tool calls, sensitive resource reads and background task operations challenge unauthenticated clients. Invalid bearer credentials fail before tool execution. Signed `tools_allowed` restrictions apply in MCP, and the backend enforces scopes, workspace membership and operation permissions using the unchanged caller credential.

Default stateless HTTP supports multiple Uvicorn workers and disables long-lived inbox notifications. Modern clients use MCP `2026-07-28` sessionless discovery; legacy clients retain the initialize handshake. Explicit stateful mode forces one worker and can enable legacy inbox subscriptions.

## Dependencies and widgets

The service pins FastMCP `4.0.10`, MCP SDK and protocol types `2.3.0`, and the optional FastMCP Tasks extension. FastMCP transports use `httpx2`; backend API calls use `httpx`. Docker uses Python 3.11. The current framework supports Python 3.10 or newer; this repository validates 3.11. MCP dependency floors are isolated from the backend service.

The MCP Apps bridge `2.0.3` and D3 `7.9.0` are vendored, with licenses and registry integrity/checksum provenance under `fastmcp_server/resources/static/vendor`. Widgets load these same-origin paths:

- `/mcp/assets/ext-apps-2.0.3.js`
- `/mcp/assets/d3-7.9.0.min.js`

No widget script needs a CDN. Optional externally hosted agent avatars still need their image origins permitted by the resource CSP. Protocol keys and resource URIs carrying the old `ax` prefix remain compatibility identifiers; visible names use Commonflame.

## Validation

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
node scripts/check-widget-assets.mjs
```

The Node check exercises the actual vendored bridge initialization/theme notifications and the graph's D3 APIs. Node 20 or newer is sufficient for this optional check; the Python server does not require Node.

For live SDK transport validation, the caller supplies a freshly approved synthetic agent token as JSON on stdin. The harness reads only `whoami` and widget resources, accepts no credential argument, and prints no credentials:

```sh
.venv/bin/python -m fastmcp_server.sdk_smoke --url http://localhost:3000/mcp
```

For a private local HTTPS CA, add `--ca-file /absolute/path/to/ca.pem`. Certificate and hostname verification remain enabled. The repository smoke runner can invoke this module with `subprocess.run(..., input=json.dumps({"access_token": token}), text=True)` using a token already held in memory. No token file or shell interpolation is needed.

See [the upgrade notes](docs/MODERNIZATION.md) for the reviewed protocol changes and primary references.
