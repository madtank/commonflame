# Authentication

Commonflame has one human account/session model and one human-sponsored OAuth
authorization model for agent hosts. Local and hosted instances use the same
contract. Settings contains preferences and workspace invitations; connecting an
agent starts at `/auth.md`, without generating or copying a PAT.

The agent onboarding contract is served at `/auth.md`; its source is
[`services/backend/auth.md`](../services/backend/auth.md). The backend renders
the configured `PUBLIC_URL` into that guide. Clients discover OAuth and
Protected Resource Metadata from the live instance.

## Human accounts

The default is `AUTH_MODE=builtin`. Local username/password accounts require no
AWS account, GitHub login, email server, or external identity provider.

For a configured loopback `PUBLIC_URL` (localhost, 127.0.0.1, or ::1), a fresh
installation opens first-owner setup in the browser. Choose a username and a
passphrase of at least 15 characters. Setup closes once any built-in account
exists, including a disabled account. A transaction lock serializes first-owner
creation. The account administers its workspace; it does not gain unrestricted
instance-wide administration.

`REGISTRATION_MODE` controls additional accounts:

| Mode | Behavior |
| --- | --- |
| `auto` (default) | Open at a loopback public origin; invitation-only at a hosted origin. |
| `open` | Anyone can create their own private workspace after owner setup. |
| `invite_only` | A workspace invitation is required. |
| `closed` | New account registration is disabled. |

Unknown values fail closed. Request Host/forwarded headers cannot turn a hosted
instance into local setup. Keep the local Docker ports bound to loopback.

At a hosted origin, first-owner setup remains operator-protected, even if
additional registration is explicitly open. The operator issues a capability:

```sh
docker compose exec backend python -m scripts.create_setup_token
```

The capability is saved to `/run/keys/owner-setup.token`, mode 0600; the command
does not print it. The operator reads the file privately and pastes it into
`/setup`. Issuance and redemption share a database transaction lock.
If an unused setup file already exists, issue a replacement using
`--output /run/keys/owner-setup-new.token`. Rotation invalidates earlier tokens.
The default expiry is one hour.

Workspace admins issue optional one-time invitations from Settings → Profile.
Invited humans create an account at `/signup` and join that workspace as members.
The server rechecks the inviter's current activity and admin membership at
redemption. Invitation secrets stay out of URLs, browser storage, and logs.
Open signup creates a separate private workspace, never automatic membership
in someone else's workspace. Account creation never approves an agent connection.

Passwords use Argon2. Browser access tokens expire after 15 minutes and stay in
tab session storage. Refresh tokens rotate in HttpOnly, host-only,
SameSite=Strict cookies with a seven-day lifetime. HTTPS deployments use Secure
cookies. Sign-out revokes the refresh session and clears browser queries.
The interactive `scripts.create_local_user` command remains an operator-only
maintenance/test helper. Normal local onboarding takes place in the browser.

## Sponsored agents

An agent reads `/auth.md`, discovers endpoints, registers its client, and hands
the human an authorization URL. Browser-capable clients use authorization code
with S256 PKCE; headless clients use device authorization. The human signs in
and deliberately approves or denies the request. Merely loading a URL or
having an existing browser session does not grant access.

The canonical resource is `PUBLIC_URL/mcp`. Standard MCP clients can connect
without inventing a named route. Consent creates a distinct identity for the
registered client, human sponsor, and approved workspace. Refresh preserves
that identity and rechecks sponsor activity and workspace membership.

The generated name uses the client's registered label plus a suffix derived
from the client ID, sponsor ID, and workspace ID. The issued access token carries
the agent ID/name and approved workspace; each MCP request presents that signed
token. Two processes sharing one registration and credential are the same agent
identity. Independent agents need separate registrations/grants and credential
stores, even when both use the same MCP host software.

Named `/mcp/agents/<name>` routes remain compatibility aliases. Routing headers
cannot turn a human token into an agent token. MCP validates signature, issuer,
canonical audience, and expiry against backend JWKS. Compose configures the JWT
issuer to match the public authorization-server origin advertised in discovery.

The public MCP catalog contains schemas. Tool calls and protected resource
reads require authentication. The instance serves its pinned MCP Apps bridge
locally, including the CSP origin that clients need to render widgets.

## Hosting and remaining work

Set `PUBLIC_URL` to the externally visible HTTPS origin. Keep signing keys,
database, and upload volumes persistent. The local recipe stays loopback-bound;
TLS, backups, deployment limits, and host policies require deployment-specific
validation before exposure.

This upgrade changes the old internal JWT issuer to the configured public
origin. Previously issued access tokens need renewal; accounts, signing keys,
and browser refresh sessions are retained. Changing `PUBLIC_URL` later likewise
requires clients to obtain new tokens for the new issuer/resource.

Built-in accounts currently have no password reset or MFA. The recommended
bring-your-own-provider interface is generic OpenID Connect: the operator
configures an issuer/discovery URL, client ID, private client secret, and callback
URL. Provider identities must bind to `(issuer, subject)`, with explicit account
linking rather than automatic linking by email. Workspace membership and agent
sponsorship stay in Commonflame. This provider adapter is future work, not an
implemented sign-in option. Cognito-specific routes/configuration are removed.

## Retired experiments

PAT creation, rotation, and exchange routes and agent client-secret management
routes are no longer mounted in the Commonflame API. The token endpoint rejects
`client_credentials`, and discovery/registration no longer offer that grant.
Legacy HMAC, cached MCP, PAT-derived JWT, standalone agent-key JWT, and developer
impersonation tokens cannot enter through either API authentication dependency.
The RLS session dependency uses the same verifier as the other API routes; old
`AX_ENFORCE_EXCHANGE`, `ENABLE_LEGACY_JWT`, and `ENABLE_LOCAL_TESTING` flags do not
restore these paths. Credential-bearing agent drafts return `410` before creating
an agent or minting a secret, including already-saved drafts.

The Credentials tab, PAT monitor cards, and experimental client-secret Agents
settings tab are removed, including their queries and mutations. The normal agent
roster, workspace invitations, widget controls, and security audit data remain.
Historical credential tables/migrations and unmounted handler source remain for
now. No credential/data migration or destructive table removal is performed.
Existing PAT or client-secret integrations must reconnect through sponsored OAuth.

Authorization code with PKCE and device authorization are two entry flows of the
same authorization server, not separate user accounts or parallel credential
products. A future CLI should use device login and a private host credential store
with automatic refresh. The CLI is not bundled in this repository.

Confidential OAuth clients may authenticate to the token endpoint using their
registered client authentication method; that proves the client identity and
never replaces a human authorization grant. Ordinary agent hosts use public
clients with PKCE/device authorization and no shared client secret.

## Bounded next steps

Prioritize a Connections view showing sponsor, agent, client, workspace, scopes,
and revoke/disconnect, plus human account recovery. Existing agent disable
controls are still enforced during API access. A connection management UI is
not implemented by this cleanup. Optional OIDC should enter the existing human
session model, without creating a second agent authorization system.

MCP authorization follows the standard OAuth discovery/PKCE direction. The
current MCP specification favors Client ID Metadata Documents; this instance's
Dynamic Client Registration remains its tested compatibility mechanism. CIMD
support and complete external-host UAT are separate follow-ups; this change does
not claim complete conformance to every current client registration mechanism.

References: [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization),
[OAuth security recommendations](https://www.rfc-editor.org/rfc/rfc9700), and
[device authorization](https://www.rfc-editor.org/rfc/rfc8628).

The guide follows the discover-and-connect `auth.md` pattern;
it does not implement WorkOS's ID-JAG protocol. See
[sponsored onboarding](SPONSORED_ONBOARDING.md) for the consent contract and
required regression evidence.
