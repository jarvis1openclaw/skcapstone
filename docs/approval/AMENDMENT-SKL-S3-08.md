# Amendment record: PostgreSQL principal scaling model

Amendment ID: `AMENDMENT-SKL-S3-08`
Card: `3825cca4` (`SKL-S3-08`)
Recorded by: `codex-skl-s3-08`
Status: proposed, pending human approval

## Baseline

The approved architecture requires multi-tenant PostgreSQL row-level
security but does not choose a scalable database-login model. The current
implemented development contract binds one PostgreSQL login to one Tenant and
Principal through `session_user`.

The hash-pinned approved documents and
`docs/approval/DESIGN-HASHES.sha256` are unchanged by this proposal.

## Proposed amendment

Add `docs/architecture/POSTGRES-PRINCIPAL-SCALING.md` as the governing
database-login scaling decision. It selects a shared transaction-pooled
`sklegal_runtime` login only when identity comes from a short-lived, one-use,
database-owned authorization-context lease minted by a separate CapAuth and
policy broker identity.

The proposal explicitly rejects caller-set tenant and Principal variables,
keeps all roles NOBYPASSRLS and outside PostgreSQL role graphs, and preserves
the existing per-principal login path until synthetic isolation and rollback
qualification is accepted.

## Approval trail effect

- No hash-pinned approved document was modified.
- The selected model is proposed architecture only until the human owner
  approves this amendment.
- Approval should record the exact hash of
  `docs/architecture/POSTGRES-PRINCIPAL-SCALING.md` and update this record's
  status.
- Implementation, production deployment, credential creation, and role
  retirement require separate eligible cards and their own evidence.
