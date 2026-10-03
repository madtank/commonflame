# Local operations and release boundaries

The supported initial mode is a local, loopback-bound Compose installation.
The development database password in `.env.example` is a documented local
placeholder. Replace it before deploying elsewhere; use URL-safe characters
because Compose constructs database connection URLs from it.

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

Before a public release:

1. Confirm the project name and license.
2. Run credential scanning, dependency audit, retained regressions, and the
   Compose smoke check from a clean checkout with empty, isolated volumes.
3. Review old source credentials for revocation at the services that issued
   them. Excluding them from this tree does not revoke their prior exposure.
4. Review outbound webhook/provider integrations, upload limits, auth controls,
   and the static frontend/MCP-app content policy.
5. Obtain Jacob's explicit authorization before pushing or publishing.

Before network deployment, add HTTPS, deployment-specific secrets, host and
redirect allowlists, backup/restore checks, access controls, observability,
and resource limits. The loopback recipe is not a production security claim.
