# Seraph review dispatch

Link materializes canonical review cards from signed lineage observations. The
card identity binds the source card, exact source head, source generation, and
candidate evidence hash. Existing matching cards are reused and ambiguous
duplicates fail closed.

Seraph runs as a bounded recurring seat on the active control-plane host. Each
cycle invokes the ordinary fleet selector with `SKFLEET_ONLY_SEAT=seraph`, a
dedicated target of one, and a host Codex limit of three on `sk-codex-mid`.
The ordinary chiap08 target remains two. One ordinary worker therefore cannot
consume the Seraph allocation, while the shared physical ceiling still prevents
the two populations from exceeding three workers. The selector then performs the existing final
admission comparison, Link recommendation, exact claim readback, worker launch,
and launch-receipt checks. Seraph reports success only after it observes exactly
one canonical `LAUNCHED` receipt, the same owner and claim revision in
CardStore, a producer-independent review card in `doing`, and its active worker
unit. Zero eligible work and zero remaining physical or provider capacity emit
distinct `NOOP_RECEIPT` reasons and are honest successful no-ops. A missing,
duplicate, or malformed receipt, stale claim, or dead process is a suppressed
failure. A producer cannot review its own candidate, and state
drift or recommendation replay prevents launch.

Canonical review cards remain excluded from every generic fleet cycle. In an
exact `SKFLEET_ONLY_SEAT=seraph` cycle, POOL_V2 may admit a backlog review card
only when its `review` and `seat-seraph` labels and complete producer and
candidate-evidence bindings pass the governed review parser. That Seraph-only
admission fact is part of both the selected and final preclaim fingerprints.
Any changed event, incomplete binding, wrong seat, or generic cycle therefore
remains non-dispatchable.

The regression test creates its canonical review card through production
`reconcile_review_work`, then runs the actual selector in one isolated child
process against a temporary CardStore. Stateful `systemctl` and `systemd-run`
shims prove the generic cycle does not claim or launch, the Seraph cycle makes
exactly one real claim and active-unit launch receipt, and replay creates no
duplicate. Test paths are discovered from the running interpreter and imported
packages so hosted CI does not depend on a workstation home directory. A
loopback gateway serves the production `/health` and `/queue` schemas, and the
portable revision probe returns the same sealed runtime revision, so the test
exercises real lane snapshot acquisition and fail-closed admission without
depending on a workstation gateway.

The packaged `skfleet-seraph.service` has a five-minute offset timer and a
five-minute service timeout. Link and Seraph retain separate cycle locks, while
CardStore creation and claim fencing provide cross-cycle convergence.
Seat-scoped selector cycles do not overwrite the generic fleet liveness and
capacity snapshot.

Installation must update the Python package, `~/.local/bin/skfleet-rotate.py`,
and the Seraph service unit from one reviewed commit, recording old and new
SHA-256 hashes before the timer is restarted. Rollback restores all three
pre-install bytes and runs `systemctl --user daemon-reload`; it does not delete
receipts. Emergency rollback is to disable `skfleet-seraph.timer`, remove
Seraph from the seat placement and control-plane records, and revert the source commit. Existing
append-only review evidence remains historical and is not deleted.
