# Local operations and release boundaries

The supported initial mode is a local, loopback-bound Compose installation.
The development database password in `.env.example` is a documented local
placeholder. Replace it before deploying elsewhere; use URL-safe characters
because Compose constructs database connection URLs from it.

With `REGISTRATION_MODE=auto`, a loopback `PUBLIC_URL` enables browser owner
setup and token-free local signup. A hosted `PUBLIC_URL` defaults to protected
owner setup and invitation-only registration. Explicit `open`, `invite_only`,
and `closed` modes control additional accounts. Changing signup policy does
not change existing workspace memberships or reopen first-owner setup.

The Postgres service uses its own `commonflame_pgdata` volume on a new default
installation (`waystation_pgdata` for an existing Waystation project). First boot builds
54 tables from the curated schema and runs schema bootstrap/migrations. It does
not connect to the prior aX database or import users, tasks, messages, or uploads.

Signing keys are generated into a private persistent volume on first boot.
Back up that volume together with Postgres and uploads. Replacing the signing
key invalidates issued credentials; coordinate rotation with clients rather
than deleting it during routine restarts.

To stop while preserving data, use `docker compose down`. Named volumes are
retained. Changing `COMPOSE_PROJECT_NAME` creates a separate installation and
does not migrate data. Keep `PUBLIC_URL` aligned with the URL used by browser and
MCP clients; OAuth redirects, discovery, and token audiences depend on it.

## Upgrading from Waystation

The repository is now `madtank/commonflame`. GitHub redirects the old repository
URL; update your checkout's remote with:

```sh
git remote set-url origin https://github.com/madtank/commonflame.git
```

Keep your existing `.env`, database password and public origin. Set
`COMPOSE_PROJECT_NAME=waystation` in that file before rebuilding. If you previously
ran without an env file, use `docker compose -p waystation` for every command.
This reuses all four original volumes: `waystation_pgdata`,
`waystation_redisdata`, `waystation_signing-keys` and `waystation_uploads`.
Do not replace your env file with the new example or run `down -v`.

The Commonflame UI and MCP metadata do not require renaming storage. The existing
Postgres database/user, refresh-cookie name, browser storage namespace and auth
locks remain stable compatibility identifiers. Stored task/message text and
user-chosen names are not rewritten.

If you want Commonflame container names while reusing the old data, first stop
the old project (`docker compose -p waystation down`, without `-v`). Save this
local override as `compose.legacy-volumes.yml`:

```yaml
volumes:
  pgdata:
    external: true
    name: waystation_pgdata
  redisdata:
    external: true
    name: waystation_redisdata
  signing-keys:
    external: true
    name: waystation_signing-keys
  uploads:
    external: true
    name: waystation_uploads
```

Then run:

```sh
docker compose -p commonflame -f docker-compose.yml \
  -f compose.legacy-volumes.yml up -d --build --wait
```

Use the same project/files
for later operations. Never run both projects against the same volumes at once.
The override requires the old volumes to exist and does not copy or delete data.
Back up before any optional storage migration; keep signing keys and uploads
together with the database.

## Rebuild and host

After editing source, rebuild images and explicitly recreate the services:

```sh
docker compose build
docker compose up -d --force-recreate --wait
```

This also avoids stale running containers on Compose versions that build an
updated image without replacing a dependent service. Named volumes persist.

The finite source-release checks and remaining publication decision are recorded
in [RELEASE.md](RELEASE.md). Sharing the local-first alpha does not require
implementing the optional hosting features.

Before network deployment, add HTTPS, deployment-specific secrets, host and
redirect allowlists, backup/restore checks, access controls, observability,
and resource limits. The loopback recipe is not a production security claim.
