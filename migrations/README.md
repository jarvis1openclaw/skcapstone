# Database migrations

SKLegal uses ordered SQL migrations with a digest-pinned manifest. Each file
uses the `NNNN_description.sql` format and contains exactly one
`-- sklegal:up` section and one `-- sklegal:down` section. The down section must
reverse only that migration. Sequences start at `0001` and remain contiguous.

Validate the static set:

```bash
make migration-check
```

Apply through a direct PostgreSQL client without putting a database URL on the
process command line. Configure libpq through environment variables,
`PGSERVICE`, or `.pgpass`; then select direct transport explicitly:

```bash
python scripts/manage_migrations.py up \
  --direct --database sklegal --user sklegal_migrator
python scripts/manage_migrations.py status \
  --direct --database sklegal --user sklegal_migrator
python scripts/manage_migrations.py down --steps 1 \
  --direct --database sklegal --user sklegal_migrator
```

The Docker transport is restricted to disposable development and test
databases:

```bash
python scripts/manage_migrations.py up \
  --docker-container sklegal-disposable-postgres
```

The runner validates that applied files and hashes are an exact prefix of the
current manifest. Up and down batches use one transaction and an advisory
lock. A failure rolls back the whole batch. The migration registry remains in
`sklegal_migrations` after a full down so drift can still be detected.

Application migrations must be owned by the exact `sklegal_migrator` role,
which must be LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT, NOCREATEROLE,
NOCREATEDB, and NOREPLICATION, with no PostgreSQL role-graph edges. The runner
rejects an unsafe role before it creates the migration registry, and verifies
every application object owner after each operation.

Provision the shared `sklegal_runtime` grantee for the CapAuth definer
functions before migration, because the migrator role cannot create roles:

```bash
python scripts/provision_postgres_runtime.py \
  --direct --database sklegal
```

Provision a pre-created least-privilege runtime LOGIN with:

```bash
python scripts/provision_postgres_principal.py \
  --direct --database sklegal \
  --runtime-role sklegal_app_tenant_one \
  --tenant-id 10000000-0000-4000-8000-000000000001 \
  --principal-id 20000000-0000-4000-8000-000000000001
```

The provisioner rejects unsafe attributes, any role-graph participation, and
SKLegal object ownership. It revokes residual privileges before granting and
reading back an exact allowlist. Applications must repeat the role drift check
at startup. RLS cannot contain a login after an administrator grants that login
`BYPASSRLS`, so administrative role governance remains a deployment trust
boundary. Production credentials and protected data never belong in migration
files or process arguments.
