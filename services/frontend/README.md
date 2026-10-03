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

Sign in at `/login` with your Waystation username and password. A fresh
localhost installation opens first-owner setup automatically at `/setup`.
Additional accounts use `/signup` without a token and get private workspaces.
An invitation is optional for joining another person's workspace. Hosted origins
keep operator-protected setup and invitation-only signup by default. See the
root docs/AUTH.md for the account and registration contract.

An agent approval link leads to backend-owned consent at `/oauth/authorize` or `/device`. Sign-in and account creation carry only a validated same-origin approval path in `next`; they return to that page so the person can explicitly approve or deny the connection. The browser keeps short-lived access credentials within the current tab; rotating refresh credentials remain in a backend-issued HttpOnly cookie.

`/app` opens the modern workspace. `/ax` remains a compatibility route for old links. Internal protocol handles, event names, and API fields remain unchanged during this first extraction.

The modern unit tests were retained. Retired marketing and legacy UI tests were removed or updated for Waystation account authentication. Production type checking excludes test fixtures; Vitest still compiles and executes the tests.
