# Authentication

The runtime integration contract is served at `/auth.md`; the source is
[`services/backend/auth.md`](../services/backend/auth.md). Configure clients from
live OAuth/Protected Resource Metadata instead of hardcoding deployed domains.

## Browser users

The operator explicitly creates accounts with `docker compose exec backend
python -m scripts.create_local_user`. There is no built-in password, open
registration, or developer impersonation in the normal stack. Each account
starts in a private workspace.

Passwords are verified with Argon2. Browser access tokens expire after 15 minutes
and are kept in tab session storage. Refresh tokens are rotating HttpOnly,
host-only, SameSite=Strict cookies with a seven-day lifetime. Cookies use Secure
on HTTPS deployments. Sign-out revokes the refresh session and clears queries;
workspace data is not persisted into shared browser localStorage.

## Agents

The backend runs the native OAuth authorization server; the MCP server is its
protected resource. Agents use a named connection URL such as
`http://localhost:3000/mcp/agents/my_agent`. First consent creates the bounded
agent identity for the sponsoring user. Device and PKCE grants preserve that
identity and the requested resource through refresh.

Named connection paths are aliases of the same MCP service. Their issued access
token audience is the canonical `http://localhost:3000/mcp`; signed agent identity
claims bind the caller rather than an arbitrary `X-Agent-Name` header. MCP
validates the signature, issuer, audience, and expiry through backend JWKS.
The stable internal issuer `ax-backend` is retained as an existing protocol ID.

The schema-only MCP catalog is public for discovery. Tool invocation and reading
protected resources require authentication. The smoke test challenges every
advertised tool without a credential before running authenticated calls.

## Scope and known limits

Local accounts enable a self-hosted quickstart; this is not a claim of a completed
internet-facing identity system. MFA, password reset, external federation, and
deployment-specific session/rate-limit review remain release work. Legacy PAT
API code is retained for compatibility but is not the recommended onboarding
path. No Gateway or CLI client is included in this distribution.
