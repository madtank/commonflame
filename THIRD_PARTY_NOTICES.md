# Third-party notices

Waystation's project license does not replace the licenses of its dependencies.

The frontend includes React, React Router, TanStack Query, Radix UI, Lucide,
Axios, DOMPurify, markdown/rendering utilities, and their dependencies. Their
license notices are collected in
[third-party-notices.txt](services/frontend/public/third-party-notices.txt),
which is also served at `/third-party-notices.txt` by the built frontend.
Build-only tools retain their notices in their npm packages.

Two unchanged JavaScript bundles are vendored in the MCP resource server:

- MCP Apps bridge 2.0.3: [license](services/mcp-server/fastmcp_server/resources/static/vendor/ext-apps/2.0.3/LICENSE) and [provenance](services/mcp-server/fastmcp_server/resources/static/vendor/ext-apps/2.0.3/PROVENANCE.md).
- D3 7.9.0: [license](services/mcp-server/fastmcp_server/resources/static/vendor/d3/7.9.0/LICENSE) and [provenance](services/mcp-server/fastmcp_server/resources/static/vendor/d3/7.9.0/PROVENANCE.md).

Python dependencies are installed from their distributions during Docker builds;
their license metadata and included notice files remain in the installed packages.
PostgreSQL/pgvector, Redis, Python, Node.js, and nginx images retain their own
upstream licensing. Requirements and lockfiles record the selected packages.
Source provenance for the application itself is documented separately in
[SOURCE_PROVENANCE.md](docs/SOURCE_PROVENANCE.md).
