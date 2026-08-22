# SKLegal hermetic source copy and evidence receipt

Card: `1dcd9f17`  
Qualification scope: exact source-copy integrity and terminal receipt semantics  
Review status: focused qualification passed

## Acceptance evidence

The existing `scripts/clean_room_check.py` harness was qualified without changing the accepted source inventory or expanding the clean-room scope.

- Exact Git-eligible source selection is performed by the allowlist inventory and copied with relative path and per-file SHA-256 pairs.
- The aggregate inventory digest is emitted as `source_inventory_sha256` in receipt schema `sklegal-clean-room-receipt/v6`.
- Scratch roots are validated as local, trusted, non-linked, and non-overlapping with the repository.
- Temporary workspace identity is checked before cleanup; cleanup state is emitted as `temporary_cleaned`.
- Phase timing is emitted with flushed JSON events containing `elapsed_seconds`.
- Child exit status is normalized into the terminal receipt `exit_code` and `status`.
- Containment and broker cleanup are represented by `containment_extinct`, `external_brokers_cleaned`, and `broker_resources`.
- The harness preserves lock, secret, SBOM, migration, vulnerability, and fixture gates by invoking the existing complete check command and source allowlist.

## Exact verification

```text
pytest -q tests/test_clean_room_check.py tests/test_clean_room_broker_boundary.py tests/integration/test_clean_room_containment.py
55 passed, 15 skipped, 40 subtests passed in 6.03s
```

The skipped cases require host containment capabilities and are not treated as passing evidence for those host-specific controls. No source, HammerTime Inbox, external account, or production environment was modified.
