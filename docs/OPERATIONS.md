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

The Postgres service uses its own `waystation_pgdata` volume. First boot builds
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
