# PostgreSQL principal scaling decision

Card: `3825cca4` (`SKL-S3-08`)
Status: proposed, pending human approval

## Decision summary

Adopt a shared, transaction-pooled `sklegal_runtime` login only after a
database-owned authorization-context lease replaces direct `session_user`
identity binding. Keep the existing per-principal login model as the active
contract until the amendment is approved and the replacement passes tenant
isolation, revocation, pooling, outage, and rollback qualification.

The shared login must never accept tenant or principal identity from a
caller-set PostgreSQL variable. A custom setting, request parameter, JWT
claim, HTTP header, or model output is not a database authorization fact.

## Options considered

### Per-principal login with an operational cap

The current design binds one PostgreSQL LOGIN role to one active Tenant and
Principal through `database_role_bindings`. It provides a simple database
backstop because `session_user` selects exactly one binding.

This remains suitable for the controlled pilot, but it is not the long-term
scaling model. PostgreSQL connection pools are partitioned by database user,
so a role per principal fragments the pool. It also creates credential,
provisioning, rotation, suspension, and deletion work proportional to the
number of principals. An operational cap would postpone that cost without
meeting the approved future-organization requirement.

### Shared login with caller-set session variables

Rejected. A shared login that can set `tenant_id` or `principal_id` can forge
another principal's database context. Wrapping `set_config` in application
code does not create a security boundary because the login can set custom
PostgreSQL variables directly.

### Shared login with a database-owned context lease

Selected, subject to human approval and implementation qualification. The
policy gateway creates a short-lived, one-use authorization-context lease
through a narrow SECURITY DEFINER function. The pooled application login can
activate only that opaque lease at the start of one database transaction.
Activation atomically consumes the lease and binds its Tenant, Principal,
purpose, policy revision, capability digest, correlation ID, expiry, backend
PID, and transaction ID in a database-owned table that the runtime login
cannot read or mutate directly.

`current_tenant_id()` and `current_principal_id()` derive identity only from
that active database-owned row. They do not read caller-controlled custom
settings. The row is usable only by the activating login, backend PID, and
transaction ID, and only before its trusted database expiry time.

## Required role model

- `sklegal_migrator` remains the sole owner of SKLegal schemas, relations,
  functions, and types.
- `sklegal_context_broker` is a separate service login allowed to create an
  authorization-context lease only after CapAuth and the policy gateway have
  approved the exact request. It receives no application-table privileges.
- `sklegal_runtime` becomes the shared pooled application login. It remains
  LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT, NOCREATEROLE, NOCREATEDB, and
  NOREPLICATION, owns no SKLegal objects, and participates in no role graph.
- Direct per-principal logins remain supported during migration and pilot
  rollback, then are retired only after the shared path is accepted.

The broker and runtime identities must be separate. Compromise of the pooled
runtime login must not grant authority to mint a context lease.

## Lease contract

The later implementation task must enforce all of these conditions:

1. A lease has a random opaque ID, one Tenant, one Principal, one purpose,
   one policy revision, one capability digest, one correlation ID, an issue
   time, a short expiry, a minting broker identity, and one-use state.
2. Lease creation rechecks the exact broker role, safe role attributes,
   Tenant status, Principal status, tenant membership, capability decision,
   policy revision, and trusted database time.
3. Lease activation rechecks all mutable authorization facts, atomically
   consumes the lease, and binds the context to the exact runtime login,
   backend PID, and current transaction ID.
4. Missing, malformed, expired, already consumed, wrong-login, wrong-backend,
   wrong-transaction, stale-policy, suspended, revoked, or unavailable state
   denies before application data can be read or mutated.
5. Transaction pooling is required. A context cannot survive COMMIT,
   ROLLBACK, cancellation, timeout, connection return, or backend restart.
6. The runtime login cannot SELECT, INSERT, UPDATE, or DELETE lease or active
   context rows and cannot execute the lease-minting function.
7. Audit records contain only safe references. They never contain a raw
   capability, credential, protected request payload, or source document.
8. Every mutation retains its existing idempotency and transactional outbox
   behavior.

## CapAuth reconciliation

Today, migrations grant durable CapAuth SECURITY DEFINER functions to
`sklegal_runtime`, while application RLS uses per-principal logins. The
selected model makes `sklegal_runtime` the pooled application identity but
does not let that shared identity choose a Principal. It may call CapAuth and
application functions only after the database-owned lease is active, and
each protected function must recheck the lease before use.

The context broker is not a bypass role. It can mint a lease but cannot read
or mutate a Client, Engagement, Matter, Matter Event, Party, Issue, Fact
Assertion, Evidence Item, Authority, Claim, Defense, Deadline, Task,
Communication, Work Product, Approval, or Execution Event.

## Pooling and capacity

Use transaction pooling with bounded pools per service class, not per human
Principal. Interactive API and worker traffic use separately bounded pools
even when both authenticate as `sklegal_runtime`. Pool acquisition timeout,
queue depth, lease activation failures, and transaction age are observable
without protected content.

The pilot keeps the existing per-principal path and records principal count,
active connections, pool wait, role-provisioning time, and revocation time.
Those measurements determine pool sizes, but they do not reopen the rejected
caller-set-variable design.

## Migration and rollback

Implementation requires its own claimed card and migrations. The safe order
is:

1. Add the broker role, lease tables, activation functions, and dual-mode
   identity readers without removing the per-principal path.
2. Prove cross-Tenant and cross-Matter denial, one-use activation,
   transaction cleanup, revocation races, broker outage, pool reuse, and
   database restart using synthetic data.
3. Route one simulation-only service pool through the shared path and compare
   audit and authorization results with the per-principal path.
4. Obtain human acceptance of the exact qualification evidence.
5. Retire per-principal application credentials in a separate migration only
   after rollback evidence is accepted.

Rollback before step 5 disables lease minting and returns services to their
existing per-principal logins. Rollback after step 5 requires restoring those
roles from the non-secret binding inventory and rotating credentials. No
protected Matter data is transformed by this decision.

## Approval gate

This document changes the current database authorization model and is not
effective merely because it is committed. Until the human owner approves
`docs/approval/AMENDMENT-SKL-S3-08.md`, the existing per-principal
`session_user` contract remains authoritative and no shared-login migration
may proceed.
