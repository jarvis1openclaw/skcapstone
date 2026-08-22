# chiap01 development dependencies

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
