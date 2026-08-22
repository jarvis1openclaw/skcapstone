# Corpus registry, bounded health, and deep reconciliation

This document pairs with `config/retrieval/corpus-registry-contract.json`
(card `f5ed9d24`, task `SKL-S2-05`). It defines how SKLegal replaces the
legacy unbounded corpus health scan with a materialized registry, a bounded
health reader, and a scheduled deep reconciliation job. The legacy evidence
is a corpus status script that spent more than 90 seconds scanning tens of
thousands of decomposition files before a review stopped it; the approved
architecture requires that health endpoints read materialized metadata with
a fixed time budget and never do unbounded filesystem work.

## Materialized corpus registry

`sklegal_retrieval.corpus_registry` owns the registry model. One
`CorpusRegistryEntry` per Tenant and release materializes the eight counts
pinned by the contract: source, normalized, decomposition, vector, graph,
reject, orphan, and release. Every entry also carries the core outbox
watermark (`core_watermark` and `core_event_sha256`), the projection lag,
the last-reconciled time, and `required_replay_lsn`, the watermark-to-LSN
mapping the replica gate consumes. Populating that mapping from the core
outbox is registry-producer work; `CorpusRegistryProjector` fills it on
every applied event through the `CoreWatermarkLsnIndex` port, closing the
gap the retrieval slice left for this task.

Incremental updates arrive through the core transactional outbox as
`CorpusRegistryEvent` values. Delivery is idempotent: the event idempotency
key makes duplicate delivery a no-op, events must arrive in strict sequence,
and each event carries the absolute observed count for one category rather
than a delta, so replays converge to the same materialized state.

The registry holds only counts, opaque identifiers, digests, watermarks, and
times. It never carries protected content or credentials.

## Bounded health view

`sklegal_retrieval.corpus_health.BoundedCorpusHealthReader` is the only read
path behind the user-facing corpus health endpoint. It reads exactly one
materialized registry snapshot behind a hard time budget and holds no corpus
tree, scanner, or projection backend port, so a full-tree scan on a health
request is structurally impossible on this path.

The contract pins the fixed health budget at 100 ms. The store read runs on
a daemon worker thread joined for at most the budget; a wedged store cannot
block the caller past the budget and cannot outlive the process. Budget
breach, store outage, or invalid state fail closed to an explicit
`unavailable` view whose counts are null, so a failed read can never be
mistaken for an empty corpus. An entry that has never been reconciled, or
lag above the pinned degradation threshold, reports `degraded`.

The health view is aggregate-only across Tenants. Per-Tenant or per-Matter
corpus detail requires the authorized API surfaces; the unauthenticated
health response never reveals Tenant existence or per-Tenant counts.

## Deep reconciliation

`sklegal_retrieval.corpus_reconciliation.DeepCorpusReconciler` is the
scheduled batch job that owns complete coverage. It is the only component
permitted to walk corpus trees and projection inventories, and it never runs
on the health path. For every release of one Tenant it reads:

- the observed corpus inventory (source pins, normalized and decomposition
  document identifiers, reject and release counts),
- the pinned release expectation from the HammerTime boundary, and
- the projection inventory (vector source identifiers, graph entity count,
  and the lexical, vector, and graph watermarks).

It recomputes each materialized count and records one typed, content-free
discrepancy per finding: missing decomposition, orphan vector, stale graph,
changed source, missing source, and count drift against the registry. It
then commits the corrected counts and the last-reconciled state through one
compare-and-set on the snapshot it read. A deadline breach or any port
failure aborts the run with no state write, a write conflict aborts the run,
and the `CorpusReconciliationReport` marks coverage `complete=False` for any
run that did not finish. Complete coverage is therefore reported separately
from health, exactly as the task requires.

## Failure rules

- Health read over budget: `unavailable`, reason `budget_exceeded`.
- Registry store outage: `unavailable`, reason `store_unavailable`.
- Malformed registry state: `unavailable`, reason `invalid_state`.
- Reconciliation timeout or input outage: no state write, incomplete report.
- Reconciliation write conflict: no state write, run aborted.

## Test coverage

- `tests/test_corpus_registry.py`: registry models, idempotent outbox
  projection, strict sequence, and the watermark-to-LSN mapping.
- `tests/test_corpus_health.py`: bounded reads, budget enforcement, timeout,
  store outage, large fixture trees, and the no-scan guarantee.
- `tests/test_corpus_reconciliation.py`: changed source, orphan vector,
  missing decomposition, stale graph, count drift, timeout, incremental
  update convergence, and complete-coverage reporting.
- `tests/test_corpus_registry_contract.py`: machine-readable contract
  validation, ASCII-dash validation, and document linkage.
