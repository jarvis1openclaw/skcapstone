# Changelog

All notable changes to **skcapstone** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- **GFS backup job + staleness monitor** (`gfs_backup.py`). A scheduled backup
  built on the existing `backup.create_backup` primitive with Grandfather-Father-Son
  retention and a health monitor. `select_gfs_retention()` is a pure function that,
  given timestamped artifacts and a `GFSPolicy` (daily/weekly/monthly/yearly counts),
  returns the keep/prune partition using borg/restic union semantics (newest per
  distinct period, per tier); an all-zero policy means "pruning disabled", never
  "delete all". `run_backup_job()` creates an artifact in a configurable dir, prunes
  with hard confinement (only `backup-*.tar.gz` files directly inside the backup dir
  are ever unlinked), and records a `gfs-state.json` sidecar. `check_backup_health()`
  reports `ok`/`stale`/`missing`/`failed` against a freshness threshold. All
  destinations/thresholds are config-driven (`config.yaml` `backup:` block or
  `SKCAPSTONE_BACKUP_*` env vars) with safe defaults (7 daily / 4 weekly / 6 monthly,
  26h threshold). New CLI `skcapstone backup gfs` and `skcapstone backup health`
  (exits non-zero when unhealthy). Zero-arg scheduler entrypoints
  `skcapstone.gfs_backup:run_scheduled_backup` and `:run_backup_monitor` for a
  `type: python` job; `make_backup_monitor_task()` logs and (with
  `SKCAPSTONE_BACKUP_ALERT=1`) fires `sk-alert`. Template systemd units in `systemd/`
  (`skcapstone-gfs-backup{,-monitor}.{service,timer}`) are inert until manually
  installed; install + `jobs.d` wiring documented in `docs/BACKUP.md`. Coexists with
  the operator shell cron (`scripts/skcapstone-gfs-backup.sh`) without touching its
  `skcapstone-state-*` output. Covered by `tests/test_gfs_backup.py` (22 tests).
- **Context-window management in the consciousness loop.** New
  `ContextWindowManager` (`context_window.py`) is wired into `ConsciousnessLoop._process`:
  after each reply it tracks per-sender cumulative token usage and, once a peer's
  history reaches 80% of `ConsciousnessConfig.max_context_tokens` (default `8000`),
  the oldest messages are summarized by the LLM into a single paragraph (keeping the
  4 most recent verbatim) and atomically rewritten. Token counting uses `tiktoken`
  (`cl100k_base`) when installed, else a `len // 4` estimate. The compression summary
  is also persisted as a durable memory (`_store_context_summary_memory`) so nothing
  is lost. Adds `ConversationStore.replace()` for the atomic whole-history rewrite and
  a new `context_stats` MCP tool (per-sender token/message counts, percent of budget,
  last-compressed timestamp), bumping the MCP tool count 124 → 125. The whole check is
  fail-safe: any error is caught and never breaks the loop.
- **Gated desktop notification on consciousness-loop responses.** After generating a
  reply the loop routes through the shared `skcapstone.notifications` path
  (`_notify_response`): title `"Agent response"`, body the first 120 chars. It is
  strictly opt-in via `SKCAPSTONE_DESKTOP_NOTIFY` (default off, checked through
  `desktop_notifications_enabled()`), so background agents never flood the desktop
  tray, and any failure is swallowed. Replaces an ad-hoc raw `notify-send` subprocess.
- **Agent systemd unit hardening.** The per-agent template `skcapstone@.service` (and
  the legacy single-agent unit, the packaged copy under `src/skcapstone/data/systemd/`,
  and the `generate_unit_file()` code path) now ship with `MemoryHigh=3G` /
  `MemoryMax=4G`, exponential restart backoff (`RestartSteps=5` +
  `RestartMaxDelaySec=300`, so restarts ramp 10s → 20s → 40s … capped at 5 min instead
  of a fixed 10s hot-loop), and a crash-loop guard (`StartLimitIntervalSec=1800` +
  `StartLimitBurst=6`) so a persistently failing daemon stops and stays failed inside
  a bounded window. Adds `OnFailure=skcapstone-alert@%i.service`: a new best-effort
  oneshot unit that always writes a visible journal event (tag `skcapstone-alert`,
  priority `err`) and opportunistically pages via `sk-alert`. This encodes the .41
  outage fix (previously hand-applied host state only) into the repo so rebuilt
  machines inherit it.
- **`coord reconcile` command + parity open-count alert.** New `coord reconcile`
  (`--apply`) converges the CardStore fold on the authoritative legacy board via
  append-only, idempotent corrective events (`card_store.reconcile_from_legacy`). The
  `coord parity` soak check now also compares store-served vs legacy open-counts and
  raises a `PARITY ALERT` when the drift exceeds `OPEN_DRIFT_THRESHOLD`, pointing at
  the `coord migrate` → `coord reconcile --apply` repair path.

### Changed
- **Model tier defaults now resolve to backend-verified models.** The default
  `ModelRouterConfig` tier map referenced Ollama names never pulled on the fleet
  (`devstral`, `deepseek-r1:8b`, `qwen3-coder`) which 404'd, and a stale
  `claude-sonnet-4-5` alt. Defaults are re-pointed to models verified live against
  Ollama `/api/tags` (`qwen3.5:4b`, `gemma3:1b`) and the SKGateway `/v1/models`
  catalog (`claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-opus-4-8`).

### Fixed
- **CardStore fold-drift.** The store fold now consumes the two sanctioned legacy
  append-only paths (`coordination/archive/<host>.jsonl` archive index +
  `coordination/card_events/*.jsonl` kanban overlay) as synthesized fold events
  (`load_legacy_mutations`), merged into each card's event stream. Mutations that only
  reached a legacy file (mirror off, or claims/completes recorded pre-cutover) are now
  seen by the fold, so `coord status` no longer overcounts open cards.
- **MCP GTD writes routed through the locked/atomic/deduped skos sink.** `gtd_tools`
  was the last writer using bare `path.write_text` (in-place truncate, no flock, no
  tmp+fsync+os.replace, no whole-store dedupe). `_handle_gtd_capture` now routes
  through `skos.gtd_ingest.capture()` (whole-store `(source, source_ref)` dedupe +
  lock + atomic save) when available; the id-keyed `clarify`/`move`/`done` mutations
  wrap their load-modify-save under the shared `_store_lock()` and persist via the
  atomic saver. Soft-imports skos's exact mechanism (with a local fallback keyed on the
  same lock) so cross-process exclusion with skos holds either way.
- **Repaired pre-existing test failures + wired the memory-promotion truth gate.**
  `fuse_mount.SovereignFS.__init__` no longer crashes with `PosixPath / None` when no
  agent resolves (mirrors `memory_engine._memory_dir` resolution). The memory-promotion
  truth-check gate (`memory_verifier.verify_before_promotion`) is now wired into the
  SHORT_TERM → MID_TERM transition in `memory_engine._promote` / `store()` and
  `PromotionEngine._promote` (fail-open when the backend is unavailable; blocked
  candidates stay in short-term). Test isolation was hardened so the suite reads no
  live `~/.skcapstone` and needs no network, and `daemon._load_components` registers
  the dreaming-job loop reference outside the scheduler-build try-block. Clean
  `pytest -m "not integration and not e2e"` run is green.

## [0.14.0] - 2026-07-03

### Added
- **Per-sender consciousness rate limiting.** The consciousness loop now
  throttles inbound message intake with a thread-safe, per-sender sliding
  window (`_RateLimiter`). Over-limit messages are skipped (logged, never
  crashing the loop); each sender has an isolated window that resets over time,
  and sender identities are normalized before counting. Configurable via new
  `ConsciousnessConfig` keys `rate_limit_enabled` (default `true`),
  `rate_limit_max_messages` (default `20`), and `rate_limit_window_s`
  (default `60.0`); a non-positive `rate_limit_max_messages` disables limiting.
- **Startup pillar-degradation health check + notify.** New
  `skcapstone.health` module (`startup_health_check` / `degraded_pillars`)
  evaluates every pillar's status at startup and emits a single `critical`
  desktop notification (reusing `skcapstone.notifications`) summarizing any
  `DEGRADED` / `ERROR` pillars. Healthy startups (all pillars `ACTIVE` or
  `MISSING`) notify nothing. Wired into `runtime.py`.
- **Message-classification logging + `consciousness classification` CLI.**
  `ConsciousnessMetrics.record_classification()` tracks per-tag counts
  (persisted in daily snapshots and surfaced in `to_dict()` /
  the `/consciousness` endpoint as `classification_usage`). The loop now emits
  an INFO `Classified message` log record (sender, tags, ~tokens, privacy) and
  records the tag distribution — observability only, routing behavior is
  unchanged. New `skcapstone consciousness classification` command shows
  today's tag distribution as a Rich table (with `--json-out`), reading the
  live daemon first and falling back to today's daily metrics file.
- **Recommended GFS backup cron + docs.** New `scripts/skcapstone-gfs-backup.sh`
  writes compressed, checksummed tarballs of the *irreplaceable* `~/.skcapstone`
  state on a Grandfather-Father-Son rotation (14 daily / 8 weekly / 12 monthly /
  2 yearly), excluding the rebuildable vector store + `index.db` and transient
  churn (comms queues, logs, skwhisper cache, media renders) so a ~0.8 GB home
  compresses to ~80 MB and the whole rotation stays a few GB. Includes a 2 GB
  free-space guard (fires `sk-alert` on low disk) and per-file `.sha256`
  sidecars. Optional **off-site 3-2-1 replication**: set `OFFSITE_DEST` in
  `~/.skcapstone/config/backup.env` and each run also `rsync`s the whole
  rotation to another host (best-effort — a failed push alerts but never fails
  the local backup). Documented in [docs/BACKUP.md](docs/BACKUP.md) alongside the
  portable `skcapstone backup` CLI, with a cross-link from
  [docs/HOUSEKEEPING.md](docs/HOUSEKEEPING.md) (backup preserves / housekeeping
  prunes) and a Documentation-table row in the README.
### Changed
- **ITIL → GTD is now a push adapter on the skos `gtd-ingest` port.**
  `itil.py::_gtd_emit()` builds `GtdCapture(source="itil", source_ref=<id>)` and
  routes incidents/problems/changes through `skos.gtd_ingest.capture()` (deduped by
  ID, idempotent), with a legacy fallback if skos isn't importable. Same sev →
  next-action/inbox routing; the store is now unified with all other GTD sources.
  See skos `docs/gtd-ingest-architecture.md` + `docs/gtd-ingest-SOP.md`.

---

## [0.13.0] — 2026-06-16

### Added
- **Legacy & broadcast comms-outbox sweep in housekeeping.** New
  `prune_legacy_comms()` sweeps the v1 outbox layouts that the v2-only
  housekeeping never reached: `~/.skcapstone/comms/outbox/<recipient>/` and
  every `~/.skcapstone/agents/<agent>/comms/outbox/<recipient>/`. Stale
  `*.skc.json` envelopes (>7d) are deleted; a v1 broadcast subdir literally
  named `*` is removed wholesale regardless of age. Wired into
  `run_housekeeping` as the `legacy_comms` target (with dry-run counting via
  `_count_stale_legacy_comms`) and surfaced in the `skcapstone housekeeping`
  CLI table.
- **Weekly housekeeping default job.** A standalone `jobs.d` drop-in
  (`config/jobs.d/housekeeping.yaml`, schedule `0 4 * * 0`) runs
  `skcapstone housekeeping` weekly as a safety net decoupled from the daemon.
  Bundled in package defaults and installed idempotently into
  `~/.skcapstone/config/jobs.d/` on a fresh `init` (never overwrites an
  existing user file).

### Fixed
- Prevents the unbounded profile growth that overheated a Framework 13 laptop
  (462k files in `~/.skcapstone`). Root cause: ~256k stale v1 `recipient="*"`
  presence-broadcast envelopes accumulating in directories literally named
  `*` under the legacy v1 outbox paths, which the existing v2 housekeeping
  never swept.

---

## [0.9.0] — 2026-03-02

### Sprint 15 — Exception Handlers, LLM Retry, Tests, Docs, Systemd, Deps
- Added structured exception handlers across CLI and daemon entrypoints
- Implemented LLM retry logic with exponential back-off in `LLMBridge`
- Expanded test suite: consciousness E2E, cross-package, agent runtime coverage
- Added `systemd` service unit template with watchdog dependency and consciousness flags
- Updated `pyproject.toml` dev dependencies: `pytest-cov>=4.0`, `pytest-asyncio>=0.21`
- Improved inline documentation and docstrings across all pillars

### Sprint 14 — Production Hardening
- ACK (acknowledgement) protocol for reliable SKComm message delivery
- Message deduplication layer prevents duplicate processing under inotify storms
- Input validation hardened on all daemon API endpoints
- Inotify watcher now auto-restarts on `OSError` (inotify limit exceeded)
- Reduced false-positive self-healing triggers via smarter health-check thresholds

### Sprint 13 — CPU Inference Optimization, Daemon E2E, Ollama Fixes
- CPU-only inference path: batching, thread pinning, reduced context window for low-RAM hosts
- End-to-end daemon test: start → send SKComm message → verify LLM response in < 60 s
- Fixed Ollama model-not-found error when model name included `:latest` tag
- `skcapstone daemon start` now waits for Ollama readiness before accepting messages
- `consciousness status` CLI command shows live backends, message counts, and conversation count

### Sprint 12 — Fallback Cascade Fix, llama3.2 FAST Tier, Timeout Scaling
- Fixed `LLMBridge.generate()` fallback cascade — passthrough tier was never reached
- `llama3.2` (2 GB) promoted to primary FAST tier for CPU-only hosts
- Response timeout now scales linearly with model size (configurable via `SKCAPSTONE_TIMEOUT_SCALE`)
- Tailscale transport hostname matching switched to exact match (fixes substring collision)

### Sprint 10–11 — Model Tier Fixes, Context Loader, Exports, Flutter UI
- Three-tier model routing: FAST (`llama3.2`) → STANDARD → CAPABLE (configurable)
- `context_loader.py`: injects agent identity and recent memories into system prompt
- Clean public exports from `skcapstone.__init__` (`ConsciousnessLoop`, `LLMBridge`, etc.)
- Flutter dashboard: agent status card, consciousness badge (online/offline), message feed
- `skcapstone coord` CLI surface: `status`, `claim`, `complete`, `list`

### Sprint 9 — Consciousness Loop, Prompt Adapter, Self-Healing
- `consciousness_loop.py`: autonomous message-processing loop backed by SKComm inotify watcher
- `prompt_adapter.py`: `ModelProfile` + `PromptAdapter` normalise prompts across Ollama model families
- `self_healing.py`: `SelfHealingDoctor` monitors pillars, auto-remediates common faults
- `ConsciousnessConfig` dataclass — YAML-driven configuration for all loop parameters
- `/consciousness` HTTP endpoint exposes live status (backends, counters, conversations)

---

## [0.1.0] — 2025-11-01 (initial release)

### Added
- Core pillar scaffold: identity, memory, trust, security, sync, skills
- `skcapstone status` CLI with Rich table output
- MCP server with `memory_store`, `memory_search`, `coord_status`, `coord_claim` tools
- CapAuth PGP fingerprint identity verification
- Coordination board (YAML-backed): tasks, agents, priorities
- `skcapstone context --format claude-md` for Claude Code integration
