# auth.md

Waystation is a self-hosted workspace for people and agents. Connect agents
through MCP OAuth; a human sponsor approves each first connection in the
browser. The MCP host stores refreshable credentials outside the agent prompt.

Use the same public origin for the UI, authorization server and MCP endpoint.
The default local origin is `http://localhost:3000`. In the examples below,
`<origin>` means the origin configured by the deployment's `PUBLIC_URL`.
Protected Resource Metadata is the runtime source of truth for URLs and scopes.

This follows the [WorkOS agent-auth pattern](https://github.com/workos/auth.md)
with explicit human sponsorship. Anonymous agent authorization is not supported.

## Human setup

The operator creates each local user from the container:

```sh
docker compose exec backend python -m scripts.create_local_user
```

Enter a username and a password of at least 12 characters. Passwords are hashed
with Argon2; they are not printed or stored as plaintext. Sign in at
`<origin>/auth/login`. There is no default administrator password or public
signup endpoint. Each user gets a private workspace and its owner membership.
The browser uses 15-minute access tokens and rotating 7-day HttpOnly refresh
cookies. HTTPS deployments mark refresh cookies Secure; cookies are host-only
and SameSite=Strict. Never share a browser token with an agent.

## Discover the authorization server

Pick a clear, unique agent handle. Use the named route:

```text
<origin>/mcp/agents/{agent_name}
```

An unauthenticated MCP request returns `401` with a `WWW-Authenticate` header.
Follow its `resource_metadata` URL, then the metadata's
`authorization_servers` URL to `/.well-known/oauth-authorization-server`.

For this distribution, native discovery advertises:

- `/oauth/register`: public OAuth client registration
- `/oauth/authorize`: authorization code flow with S256 PKCE
- `/oauth/device/code`: headless device code flow
- `/oauth/token`: token exchange and refresh
- `/.well-known/jwks.json`: public token verification keys

Use the returned metadata rather than assuming any endpoint or scope exists.
The base MCP resource is `<origin>/mcp`; agent device requests must target the
named route instead of the base resource.

## Recommended agent flow: device code

1. Register a public client with no client secret and no redirect URI:

   ```http
   POST <origin>/oauth/register
   Content-Type: application/json

   {
     "client_name": "My agent host",
     "redirect_uris": [],
     "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"],
     "response_types": [],
     "token_endpoint_auth_method": "none",
     "scope": "openid offline_access ax-api/mcp:read ax-api/mcp:write"
   }
   ```

2. Request a device code with the returned `client_id` and the agent resource:

   ```http
   POST <origin>/oauth/device/code
   Content-Type: application/x-www-form-urlencoded

   client_id=<client_id>&resource=<origin>/mcp/agents/{agent_name}&scope=openid%20offline_access%20ax-api/mcp:read%20ax-api/mcp:write
   ```

   Form-encode the actual resource URL. Display `verification_uri_complete` and
   `user_code` to the human sponsor. Never request their password in agent chat.

3. The human opens the approval URL, signs in, reviews the client, agent,
   resource and scopes, and presses **Approve Connection**. A pending device
   code does not authorize an agent until this deliberate action.

4. Poll `/oauth/token` no faster than the returned `interval`:

   ```http
   POST <origin>/oauth/token
   Content-Type: application/x-www-form-urlencoded

   grant_type=urn:ietf:params:oauth:grant-type:device_code&client_id=<client_id>&device_code=<device_code>&resource=<origin>/mcp/agents/{agent_name}
   ```

   `authorization_pending` means continue waiting; `slow_down` means increase
   the interval. Stop on denial or expiry and ask the sponsor to start again.

5. Save the returned access token, refresh token, expiry, token type, scopes
   and OAuth client metadata in the MCP host's vault or an OS credential store.
   Never commit them to git, paste them into prompts, or log token responses.

6. Connect to the named MCP route with `Authorization: Bearer <access_token>`.
   Refresh before expiry through `/oauth/token` with `grant_type=refresh_token`
   and the same client and resource. Replace the stored refresh token whenever
   it rotates. Two independent refresh owners require two independent mints.

## Browser-capable MCP hosts

Register a public client with the host's exact callback URI and
`authorization_code` plus `refresh_token` grants. Generate a high-entropy PKCE
verifier, use S256, bind a random `state`, and open the discovered authorization
endpoint with the client, resource, redirect URI, scope and challenge.

Waystation shows a browser sign-in and approval page before issuing a code.
Check `state` at the callback. Exchange the code at `/oauth/token` with the
original `redirect_uri`, `client_id`, resource and `code_verifier`.
Never accept an authorization code from an unexpected callback.

## Tool behavior and live events

Read `ToolAnnotations` before invoking tools. Tool descriptions and annotations
describe reads, writes and destructive operations; they do not replace the
human's authorization for the underlying task. Identity and space authorization
are checked by the backend and MCP server, not inferred from client text.

Use separate OAuth credentials for long-lived SSE listeners. Connect to
`GET <origin>/api/sse/messages` with a Bearer header and the authorized space.
Events are space-scoped, so filter for the intended agent and wake on relevant
mentions. Run listeners under the host's process monitor, refresh proactively,
and reconnect with bounded backoff. Do not store tokens in the MCP server.

## Operator boundaries

Signing keys persist in the private `signing-keys` Docker volume and are never
included in the repository. Losing or replacing that volume invalidates current
access tokens. Database, Redis and uploads have separate persistent volumes.
Configure `PUBLIC_URL` consistently before authorizing remote clients.

The compatibility audience/scope identifiers `ax-api`, `ax-mcp` and `ax-backend`
remain internal protocol names. They do not require an aX cloud account.
Legacy PAT management remains available for compatibility; prefer OAuth for new
agent connections. This first release has no cloud identity dependency.
