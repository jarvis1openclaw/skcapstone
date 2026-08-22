# chiap01 development dependencies

## Read-only progress UI

`compose.progress-ui.yml` serves the public SKLegal delivery snapshot at
`http://127.0.0.1:15174/status/`. The service binds only to loopback, runs as
an unprivileged user with all Linux capabilities dropped, uses a read-only
container filesystem, and mounts only the status page plus four approved or
public planning records. It does not serve the whole repository or any Client
or Matter data.

Validate and start it from the repository root:

```bash
docker compose -f deploy/chiap01/compose.progress-ui.yml config --quiet
docker compose -f deploy/chiap01/compose.progress-ui.yml up -d progress-ui
curl --fail --silent --show-error http://127.0.0.1:15174/status/ >/dev/null
```

Inspect its health and response headers:

```bash
docker compose -f deploy/chiap01/compose.progress-ui.yml ps
curl --head http://127.0.0.1:15174/status/
```

From another trusted workstation, keep the service loopback-only and use an
SSH tunnel:

```bash
ssh -L 15174:127.0.0.1:15174 chiap01
```

Then open `http://127.0.0.1:15174/status/` in the local browser.

Rollback is stateless and does not remove data:

```bash
docker compose -f deploy/chiap01/compose.progress-ui.yml down
```

This is a local progress surface, not a production SKLegal deployment. A
non-loopback bind, reverse-proxy route, protected data source, or production
activation requires a separate eligible card and its own security gates.

Run `make dev-deps` from the repository root to start the isolated PostgreSQL
and Temporal development dependencies. Both ports bind only to loopback. Live
database state uses a local Docker volume because the NFS project mount does
not provide the locking guarantees required for database files.

The image references pin both release tags and Linux x86_64 registry digests
for reproducible use on chiap01.

The loopback-only PostgreSQL service uses trust authentication for this local
development network. This definition is not a production deployment. Run
`make dev-deps-down` to remove the containers, network, and development
volume.

## Initial SKLegal PostgreSQL definition

`compose.sklegal.yml` is a separate, loopback-only initial PostgreSQL
definition. It does not activate a production deployment. From the repository
root, create its owner-controlled local environment file and replace the
password placeholder before starting the service:

```bash
cp deploy/chiap01/postgres.env.example deploy/chiap01/postgres.env
${EDITOR:?set EDITOR} deploy/chiap01/postgres.env
docker compose -f deploy/chiap01/compose.sklegal.yml config --quiet
docker compose -f deploy/chiap01/compose.sklegal.yml up -d postgres
```

The local `postgres.env` file is ignored by Git. Keep its password out of
shell history, logs, and committed files. Stop the service without deleting
its named data volume with:

```bash
docker compose -f deploy/chiap01/compose.sklegal.yml down
```
