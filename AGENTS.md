# Waystation local revival

This is a new monorepo curated from three aX source snapshots. Its working name
is provisional. Keep changes local until Jacob explicitly authorizes publishing.

- Runtime code lives under `services/backend`, `services/mcp-server`, and
  `services/frontend`. Root Compose is the only supported full-stack startup.
- No copied source Git history, old environment files, cloud infrastructure,
  credential caches, uploaded user data, database dumps, or archived marketing.
- Keep OAuth device authorization, auth.md, stateless MCP, and the modern UI.
- Preserve meaningful auth and regression tests. Old tests must be evaluated,
  not discarded merely because they are old.
- Default browser auth uses explicitly created local accounts. Never enable
  anonymous developer impersonation in the normal stack.
- Prefer same-origin frontend requests and make external providers opt-in.
- Never log access tokens, passwords, private keys, or bearer headers.
- Test fresh database bootstrap, browser login, OAuth device authorization,
  MCP tools, persistence across restart, and task/message round trips.
- This stack binds to loopback. Internet deployment needs a separate review.
