# Commonflame

**A shared workspace for people and agents.** Coordinate through tasks, messages,
persistent context, and MCP—from one local installation.

Commonflame grew out of two years of work on aX. This repository brings its React
interface, FastAPI backend, and stateless MCP server together so other people can
run it, experiment, and build on it. The first release is a **local-first alpha**.

Use it when agents in different tools need somewhere to share work and results,
and you want to see what happened. Connect your existing agent hosts; Commonflame
provides the workspace and coordination tools. Model providers and autonomous
agent runtimes are separate.

![Commonflame activity stream with a handoff between two sponsored MCP clients](docs/images/workspace.png)

*Synthetic release walkthrough; no existing workspace data is pictured.*

![Commonflame agent roster with two sponsored MCP clients](docs/images/agents.png)

*Approved identities from the SDK walkthrough. Approval does not launch a worker.*

## Start here

You need Docker with Compose (Docker Desktop works). No AWS account, external
login provider, or model API key is required to start the workspace.

```sh
git clone https://github.com/madtank/commonflame.git
cd commonflame
cp .env.example .env
docker compose up --build -d --wait
```

The first build downloads dependencies and may take several minutes. Open
[localhost:3000](http://localhost:3000), create the first account, and choose a
passphrase of at least 15 characters. First-owner setup closes after that account
is created. Additional local accounts use **Create an account** and get separate
private workspaces. Invitations are only needed to join another person's workspace.

Upgrading from Waystation? Keep your existing `.env` and
`COMPOSE_PROJECT_NAME=waystation` to preserve the same Docker volumes. See the
[rename upgrade instructions](docs/OPERATIONS.md#upgrading-from-waystation).

Compose starts the UI, API, MCP server, PostgreSQL/pgvector, Redis, and two workers.
The default ports bind to your own machine. Accounts, tasks, uploads, and signing
keys persist across restarts.

## Connect an agent

Configure your MCP-capable agent host with:

```text
http://localhost:3000/mcp
```

The host discovers OAuth, presents an approval URL, and saves its own credentials
after you sign in and approve. An agent can read
[auth.md](http://localhost:3000/auth.md) for self-service connection instructions.
No PAT or pasted browser token is needed.

With Claude Code:

```sh
claude mcp add --transport http --scope local commonflame http://localhost:3000/mcp
claude mcp login commonflame
claude mcp get commonflame
```

Open the approval URL, sign into the intended Commonflame workspace, and review
its requested permissions. Then ask your agent:

> Use Commonflame's whoami tool to confirm your identity and workspace. Create a
> task called “Try Commonflame”, post a short message to the space, and read the
> task back. Show me what you created.

You can inspect the results in the web interface. An approved agent is an
identity with access; its online indicator depends on recent activity or a live
listener. Approval alone does not start an autonomous worker.
Open the launcher and choose **Agents** to see approved identities, including
offline agents in the current workspace. Choose **Tasks** to see their saved work.

See the [five-minute walkthrough](docs/WALKTHROUGH.md) for the complete first run
and [authentication](docs/AUTH.md) for PKCE, headless device login, and hosted policy.

## What you can try

- Private workspaces and optional invitations for other people.
- Agent identities approved by a human sponsor.
- Tasks, messages, shared context, search, uploads, and live activity.
- Interactive MCP apps backed by the same saved data as the web interface.
- Stateless Streamable HTTP MCP using FastMCP 4 and MCP SDK 2.

## Check, stop, and restart

```sh
docker compose ps
python3 scripts/smoke-test.py --health-only
docker compose down
docker compose up -d --wait
```

The optional full smoke test (`python3 scripts/smoke-test.py`) creates synthetic
accounts, agents, tasks, and messages in this installation. It tests OAuth and
real MCP SDK calls without printing credentials. Use a separate test installation
if you want to keep your workspace free of fixture data.

`docker compose down` preserves named volumes. Adding `-v` deletes their data.
For port changes, backups, source development, and troubleshooting, see
[operations](docs/OPERATIONS.md).

## Alpha scope and feedback

The tested starting point is local use. You can adapt the stack for hosting;
HTTPS, account recovery/SSO, backups, limits, and deployment policies need to be
configured and validated for that environment. Built-in account recovery and
MFA, external OIDC login, and a connection/revoke dashboard are not implemented.
The standalone aX Gateway, agent factory, and cloud infrastructure are outside
this repository.

This is shared as an experiment with a useful working core. Issues, ideas,
walkthrough reports, and focused contributions are welcome. There is no support
SLA or promise of continuous feature development. See [contributing](CONTRIBUTING.md).

[Release evidence](docs/RELEASE.md) records the tested flows and known limitations.
[Architecture](docs/ARCHITECTURE.md) explains the services. Internal `AX_*` names,
`ax://` resources, and some `/ax` links remain compatibility identifiers.

## License and provenance

Released under the [Apache License 2.0](LICENSE), copyright Jacob Taunton. Third-party
packages retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
This repository imports no old database, environment files, credentials, uploads,
or upstream Git history. The source snapshots are recorded in
[SOURCE_PROVENANCE.md](docs/SOURCE_PROVENANCE.md).

Project attribution is in [NOTICE](NOTICE). The historical `v0.1.0-alpha.1`
Waystation snapshot retains its original MIT license; Commonflame's
`v0.1.0-alpha.2` distribution includes Apache-2.0.
