# v0.1.0-alpha.1 release evidence

Verified locally on October 3, 2026. This is a local-first alpha: a working
coordination workspace shared for experimentation and feedback, with no support
SLA or commitment to an ongoing feature roadmap.

## Acceptance boundary

The release is complete when a clean source checkout starts with an empty
database, a person can create an account, an existing agent host can connect
through sponsored OAuth, and tasks/messages can be created and read through MCP
and the interface. The README and walkthrough must describe that actual flow.
Publication additionally requires the owner's project-license decision.

## Regression results

| Check | Result |
| --- | --- |
| Backend supported unit regressions | 130 passed; 32 historical database integration tests deselected |
| Frontend regressions | 1,130 passed; 3 skipped |
| Frontend type check and production build | Passed |
| MCP regressions | 577 passed, plus 369 subtests |
| Bundled MCP Apps and D3 asset/API check | Passed |
| Docker images for all seven services | Built successfully |

The retained historical integration tests require their original database
fixtures and are outside the supported local test command. They remain in the
source; fresh-database behavior is exercised by the full-stack smoke instead.
Library deprecation warnings and frontend build-size warnings remain.

## Installation and connection evidence

An isolated source-only clone with new Compose volumes passed the full smoke:
first-owner setup, signup/private workspaces, optional workspace invitations,
login/refresh/logout, explicit OAuth PKCE/device approval and denial, binding and
replay rejection, actual MCP SDK 2 tools/resources, tasks/messages, scoped SSE,
and rejection of retired PAT/client-secret routes. The current and legacy MCP
protocols are exercised without a persistent MCP HTTP session.

The release check also requires an actual agent-created MCP task and saved
agent-authored message to be readable by the human. It verifies that the
in-space Agents widget includes approved offline identities. This caught and
fixed missing agent-name/workspace claims in OAuth tokens and an online-only
roster query that had made the widget's All filter appear empty.

Installed Claude Code 2.1.229 completed its own OAuth discovery, dynamic client
registration and PKCE login against an isolated installation, then reported the
MCP server **Connected**. Its test profile was isolated from existing provider
credentials. No model inference or autonomous worker run was part of this check.
Other complete agent-host login flows have not been verified. The SDK smoke
creates distinct sponsored identities and invokes real tools.

The corrected source passed a second fresh-volume installation and the stronger
smoke. Browser verification showed the saved agent message, MCP-created task
and approved agents in the in-space roster. Screenshots use synthetic data only.
Restart preserved the account, signing keys, issued access token, refresh cookie,
task and message. The main localhost installation was updated without resetting
its volumes; temporary QA stacks were stopped with their volumes preserved.

Earlier localhost HTTPS testing verified Secure refresh cookies, discovery and
real SDK calls through a verified TLS origin. That is deployment evidence for
the recipe, not certification of an Internet-facing installation.

## Dependency audit

The runtime audits after patching reported zero known vulnerabilities for the
frontend production dependency tree and MCP image. The backend has one unique
remaining advisory: `CVE-2024-23342` / `GHSA-wj6h-64fc-37mp` in `ecdsa==0.19.2`,
a transitive dependency of `python-jose==3.5.0`. The audit lists it twice because
both identifiers refer to the same advisory. There is no patched upstream
version. See the [upstream advisory](https://github.com/tlsfuzzer/python-ecdsa/security/advisories/GHSA-wj6h-64fc-37mp).

The exception is bounded to the current authentication configuration: signing
uses PyJWT/cryptography and RS256; verification explicitly permits RS256 only.
The application does not use ECDSA signing, key generation or ECDH in that flow.
This does not make the dependency generally safe for those other uses.
Reevaluate before changing permitted algorithms or using ECDSA functionality.

The full frontend audit also reports 18 affected development/build packages
(12 high, five moderate and one low). They include the Tailwind 3 tooling,
Faker and Vitest toolchain; the production dependency audit is separate and
reports zero. These tools run during development/build/tests and are absent
from the shipped nginx runtime. Automatic nonbreaking npm remediation failed
with a dependency-tree error; broader toolchain migration is deferred. Build
only trusted source and reassess these findings before accepting untrusted
build inputs or exposing development servers.

Audits reflect available advisories on the test date and cannot guarantee the
absence of undiscovered vulnerabilities. Dependency license texts are preserved
in THIRD_PARTY_NOTICES.md and the frontend's served notices file.

## Known alpha limits

- Built-in account recovery/MFA, upstream OIDC and a connection/revoke dashboard
  are not implemented. Hosting needs explicit operator policy and validation.
- Agent approval creates a sponsored identity; it does not launch a listener or
  autonomous worker. The agent host executes tools when asked.
- Cloud infrastructure, standalone Gateway, agent factory and model providers
  are outside this repository. Compatibility `AX_*`/`ax://` identifiers remain.
- With no AI provider configured, optional summary prefetch can return 503 and
  log a browser warning/error. Messages and tasks continue to work; automatic
  AI summaries are outside the verified local core.
- CI is configured, but GitHub Actions has not run before the first push.
- The owner has confirmed retained rights to the original source. The project
  license is awaiting the owner's decision. Nothing has been published yet.

Future improvements can be issues or contributions; they do not extend this
release's acceptance boundary.
