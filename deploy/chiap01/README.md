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
