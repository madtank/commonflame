# Source provenance

The revival uses fresh, detached snapshots of fetched `origin/main`. The
original working directories and their local edits remain untouched.

| Service | Source repository | Commit | Commit date |
| --- | --- | --- | --- |
| Backend | ax-platform/ax-backend | `7de2e3bbf06e1f92120938c30006e892030aab9a` | 2026-07-08 |
| MCP | ax-platform/ax-mcp-server | `623ec6a506cd6d0497141395405b453ba693229f` | 2026-07-08 |
| Frontend | ax-platform/ax-frontend | `e32efe7ad6b4c992ac594d1dfc54eb8cf09c7756` | 2026-07-29 |

Source Git history is excluded from this new repository. Source checkout
locations are local bookkeeping and are not part of the distributable runtime.
Curated runtime code retains internal protocol identifiers where necessary.
Third-party dependencies keep their own licenses; a project license does not
replace those dependency obligations.
