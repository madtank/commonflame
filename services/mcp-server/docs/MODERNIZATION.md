# MCP modernization

Versions were checked against the official package registries on 2026-10-03. The runtime moves from FastMCP 3.3.1 to FastMCP 4.0.10 and MCP SDK/types 2.3.0. Browser widgets keep their existing custom HTML implementation, using locally served MCP Apps bridge 2.0.3 and D3 7.9.0.

## Reviewed changes

- Modern HTTP is sessionless and uses `server/discover`; legacy initialize remains available. The SDK validates modern protocol/method/name routing headers. The service's legacy public-catalog shortcut yields modern traffic to the SDK.
- Imports use public FastMCP 4 locations for tool results, authorization checks, task configuration and errors. Python protocol fields use snake_case; JSON wire fields keep their standard spelling.
- Background tasks register the explicit `TasksExtension` and use configured Redis. The extension scopes task records by authenticated client and subject. Sensitive task and subscription methods also pass the service's early bearer gate.
- Optional legacy resource subscriptions use SDK 2 explicit request contexts and public handler registration. The default stateless service does not advertise them.
- MCP trusts the native backend OAuth server through JWKS. Cognito client/audience identity heuristics and obsolete framework import fallbacks are removed. Only signed agent identity establishes agent authorship; native browser credentials stay human-authored even on named transport aliases.
- Canonical resource audience remains the exact public `/mcp` URL. Named aliases do not expand accepted audiences. JWT issuer and OAuth metadata issuer share the configured public origin, without claiming broader JWT-profile compliance.
- Bundled action tools can perform reads and writes. MCP forwards the original bearer token, allowing backend action-specific scopes and permissions to enforce each operation; it does not replace it with a service credential.
- Widget libraries are integrity-verified official npm distributions with licenses and provenance. Runtime resource CSP permits the configured public origin instead of a script CDN.

Python 3.11 remains the container/runtime validation target. FastMCP 4 requires Python >=3.10, Pydantic >=2.12 and Starlette >=1.0.1. These upgrades affect only this service's environment. Access logs remain disabled to keep legacy query credentials out of logs.

## Regression evidence

The retained protocol, tool, permission, resource and widget tests preserve their original formatting and comments. Obsolete FastMCP 3 import-layout tests and removed audience-normalization-helper tests were retired; identity tests now require signed agent claims or explicitly assert that unsigned route/client hints cannot promote a user.

New tests exercise real SDK 2 HTTP transports in legacy and modern modes, tool invocation and resource reading; real RSA/JWKS signature, issuer, audience and expiry checks; anonymous method challenges; local asset checksums; human/agent identity separation; and backend scope-denial propagation. The live `fastmcp_server.sdk_smoke` harness accepts a synthetic OAuth credential on stdin and supports verified HTTPS with `--ca-file`.

Container startup, metadata-driven OAuth bootstrap, durable task/message operations and browser UAT belong to the repository's integration smoke and are validated separately against the rebuilt application.

## Primary references

- [FastMCP documentation index](https://gofastmcp.com/llms.txt)
- [Upgrading from FastMCP 3](https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3)
- [FastMCP HTTP deployment](https://gofastmcp.com/deployment/http)
- [FastMCP custom HTML Apps](https://gofastmcp.com/apps/low-level)
- [FastMCP token verification](https://gofastmcp.com/servers/auth/token-verification)
- [Current MCP transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
- [Current MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [JWT access-token validation, RFC 9068 section 4](https://www.rfc-editor.org/rfc/rfc9068.html#section-4)
- [FastMCP registry](https://pypi.org/project/fastmcp/4.0.10/), [SDK registry](https://pypi.org/project/mcp/2.3.0/), [protocol types](https://pypi.org/project/mcp-types/2.3.0/)
- [MCP Apps SDK registry](https://registry.npmjs.org/@modelcontextprotocol%2fext-apps/2.0.3), [D3 registry](https://registry.npmjs.org/d3/7.9.0)
