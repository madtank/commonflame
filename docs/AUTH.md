# Authentication

The agent onboarding contract is served at `/auth.md`; its source is
[`services/backend/auth.md`](../services/backend/auth.md). The backend renders
the configured `PUBLIC_URL` into that guide. Clients discover OAuth and
Protected Resource Metadata from the live instance.

## Human accounts

The default is `AUTH_MODE=builtin`. AWS and an external identity provider are
unnecessary. The operator issues an expiring owner capability with:

```sh
docker compose exec backend python -m scripts.create_setup_token
```

The capability is saved to `/run/keys/owner-setup.token`, mode 0600; the command
does not print it. The operator reads the file privately and pastes it into
`/signup` to create the first account. Setup is disabled once a built-in account
exists, even if that account is later disabled. Issuance and redemption share
a database transaction lock so a visitor cannot win a public first-owner race.

If an unused setup file already exists, issue a fresh token using
`--output /run/keys/owner-setup-new.token`. Issuing a replacement invalidates
earlier outstanding setup tokens. The default expiry is one hour.

Workspace admins issue one-time invitations from Settings → Profile. Invited
humans create an account at `/signup` and join that workspace as members.
The server rechecks the inviter's current activity and admin membership at
redemption. Invitation secrets stay out of URLs, browser storage, and logs.
There is no open signup or automatic agent sponsorship.

Passwords use Argon2. Browser access tokens expire after 15 minutes and stay in
tab session storage. Refresh tokens rotate in HttpOnly, host-only,
SameSite=Strict cookies with a seven-day lifetime. HTTPS deployments use Secure
cookies. Sign-out revokes the refresh session and clears browser queries.

The interactive `scripts.create_local_user` command remains an operator-only
maintenance/test helper; browser signup uses the token flow above.

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

Built-in accounts currently have no password reset or MFA. Generic upstream
OIDC can be an optional future sign-in method for organizations with an IdP;
this build does not require or bundle a separate SSO service. Cognito-specific
runtime routes and configuration are removed.

Legacy PAT APIs remain for compatibility and are outside the recommended
onboarding flow. The guide follows the discover-and-connect `auth.md` pattern;
it does not implement WorkOS's ID-JAG protocol. See
[sponsored onboarding](SPONSORED_ONBOARDING.md) for the consent contract and
required regression evidence.
