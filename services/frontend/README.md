# Waystation frontend

The modern React workspace for people and agents. The production build uses Node 24 and Vite 8. The root Compose stack serves this app at `http://localhost:3000` and routes API/MCP traffic through the same origin.

```sh
npm ci
npm run build
npm run test:run
npm run type-check
```

For local frontend development against a running root stack:

```sh
VITE_PROXY_TARGET=http://localhost:3000 VITE_MCP_PROXY_TARGET=http://localhost:3000 npm run dev
```

Create an installation account through `docker compose exec backend python -m scripts.create_local_user`, then sign in with its username and password. The browser keeps short-lived access credentials within the current tab; rotating refresh credentials remain in a backend-issued HttpOnly cookie.

`/app` opens the modern workspace. `/ax` remains a compatibility route for old links. Internal protocol handles, event names, and API fields remain unchanged during this first extraction.

The modern unit tests were retained. Retired marketing, Cognito login, and legacy UI tests were removed or updated for the new local login. Production type checking excludes test fixtures; Vitest still compiles and executes the tests.
