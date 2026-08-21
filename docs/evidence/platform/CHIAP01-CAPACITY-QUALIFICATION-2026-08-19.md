# chiap01 capacity and deployment baseline qualification

Card: `ad941ffe`, `SKL-S0-02`  
Observation time: 2026-08-19T21:41:47-05:00  
Observer: `codex-qualify`  
Method: read-only host, Docker, process, filesystem, and service inspection  
Decision state: qualified for independent review

## 1. Qualification result

The current chiap01 baseline passes the SKLegal capacity policy. The former 95 percent root utilization snapshot is obsolete. Root is now at 54 percent utilization with 400.88 GiB available. The SKLegal project path resolves to the separate OneDrive NFSv4 mount, which is at 54 percent utilization with 1463.39 GiB available.

The NFS-first deployment is safe for the repository, frontend, backend source, virtual environments, package installations, generated work products, exports, and backup files. Docker image layers and live transactional persistence remain local to chiap01 unless a separately qualified remote database is selected. Live PostgreSQL and Temporal database files must not be placed directly on the current NFS mount because it advertises `local_lock=none`.

The policy reserves 40 GiB of local capacity and 150 GiB of project NFS capacity for the initial deployment. After those reserves, the current snapshot retains 360.88 GiB locally and 1313.39 GiB on the project mount. No storage expansion or cleanup is required to begin the controlled development deployment.

## 2. Filesystem and hardware baseline

| Resource | Current fact | Qualification |
|---|---:|---|
| Root `/` | 912.81 GiB total, 400.88 GiB available, 54 percent used | Pass |
| SKLegal mount `/mnt/cloud/onedrive` | 3163.56 GiB total, 1463.39 GiB available, 54 percent used | Pass |
| Root inodes | 3 percent used | Pass |
| OneDrive NFS inodes | 1 percent used | Pass |
| CPU | AMD Ryzen 9 7950X3D, 32 logical processors | Pass |
| Memory | 61.95 GiB total, 32.18 GiB available | Pass |
| Swap | 8 GiB total, 7.96 GiB free | Pass |
| Uptime and load | 20 days, load averages 0.29, 0.34, 0.31 | Pass |

`/mnt/cloud` is part of the root filesystem. The nested `/mnt/cloud/onedrive` path is the NFS mount. Deployment paths must use the full `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal` prefix. A path under another unmounted `/mnt/cloud` child would consume root storage.

The NFS source is `10.0.0.111:/share/cloud/onedrive`. Important mount properties are NFSv4.2, `hard`, `nconnect=8`, and `local_lock=none`.

An unmounted 931.5 GiB Samsung 990 PRO remains available as a future expansion candidate at `/dev/nvme0n1p1`. It has a DOS partition table but no recognized filesystem signature. It is not needed for the current SKLegal budget and must not be formatted without a separate preservation review and authorization.

## 3. Current storage consumers

| Consumer | Observed size or state | Notes |
|---|---:|---|
| `/home` | 212 GiB | Largest root directory after cleanup |
| `/var` | 155 GiB | Includes containerd and logs |
| `/usr` | 78 GiB | Operating system and packages |
| `/opt` | 15 GiB | Existing host software |
| `/var/lib/containerd` | 120 GiB | 31 GiB content and 90 GiB snapshots |
| Docker images | 128.6 GB | 73.63 GB reported reclaimable |
| Docker containers | 4 total, 3 active, 111.1 MB writable data | No SKLegal container exists |
| Docker build cache | 938.3 MB | No cleanup performed |
| `/var/log` | 29 GiB | Includes 4.1 GiB filesystem journal |
| `/var/log/syslog.1` | 17.03 GB | Largest single log file |
| Local model directories | 67 GiB plus 50 GiB | Existing non-SKLegal assets |
| Hugging Face cache | 4.6 GiB | Active Whisper bind mount, do not prune blindly |

The Docker and containerd sizes overlap. They must not be added together as separate reclaimable capacity. No image, cache, log, model, or volume was deleted.

## 4. Docker volume and database reconciliation

Docker reports one named volume, `skmemory_skmem_pg_data`, at 199.1 MB. It is referenced by the active `skmem-pg` container and no orphan named volume was found. All observed active-container bind sources exist.

| Container | State | Persistence | Ownership evidence |
|---|---|---|---|
| `skmem-pg` | Running and healthy | Named volume plus two read-only init binds | Compose project `skmemory`, working directory `/home/skuser01/.local/share/skmemory-runtime` |
| `hammertime-whisper-large-v3` | Running | Read-only Hugging Face and application binds | Launched from the `skuser01` user service `whisper-large-v3.service` |
| `kokoro-tts` | Running | No named volume | Launched from the `skuser01` user service `kokoro-tts.service` |
| `llm-scaler` | Exited | Model bind retained | Not counted as active volume ownership |

The current PostgreSQL listener is bound to `127.0.0.1:5432` for SKMemory. The `skmemory` database is about 87 MB. SKLegal must use a separate database or cluster and must not share the SKMemory application schema.

Uncertainty: Docker reports image and snapshot state through containerd, while filesystem accounting exposes containerd separately. Cleanup eligibility was not inferred from file age. The 73.63 GB Docker reclaimable figure is informational only and is not authorization to prune.

## 5. NFS-first deployment budget

### Local chiap01 reserve

Local storage is an upper-bound budget for host-managed state, not a requirement that the repository move off NFS.

| Local category | Initial budget |
|---|---:|
| OCI image layers and build cache | 12 GiB |
| Dedicated SKLegal PostgreSQL data and WAL | 10 GiB |
| Temporal persistence allowance | 6 GiB |
| Logs and telemetry buffers | 4 GiB |
| Bounded render and task temporary space | 6 GiB |
| Unallocated guard | 2 GiB |
| Total | 40 GiB |

### Project NFS reserve

| NFS category | Initial budget |
|---|---:|
| Repository, dependencies, and virtual environments | 20 GiB |
| Materialized references and application caches | 40 GiB |
| Generated work products, previews, and exports | 60 GiB |
| Encrypted database and configuration backup exports | 20 GiB |
| Unallocated guard | 10 GiB |
| Total | 150 GiB |

Original HammerTime corpus material remains HammerTime-owned and is not duplicated into this budget. SKLegal references immutable releases and provenance.

## 6. Capacity thresholds and deployment gates

The executable policy is `config/platform/chiap01-capacity-policy.json`.

| Check | Warning | Critical and deployment stop |
|---|---|---|
| Root used percentage | 80 percent | 90 percent |
| Root available after 40 GiB deployment reserve | Below 150 GiB | Below 100 GiB |
| OneDrive NFS used percentage | 80 percent | 90 percent |
| NFS available after 150 GiB deployment reserve | Below 500 GiB | Below 250 GiB |
| Proposed TCP port | Not applicable | Any existing listener conflict |
| Docker named volume inventory | Orphan volume | Active reference to missing volume |

Either percentage or free-space floor can raise the state. A critical result blocks new deployment and persistent migration. A warning blocks capacity-growing changes until an owner records review. The check uses captured fixtures and does not consume disk.

Recommended execution schedule after the deployment scaffold exists:

- Before every deployment and migration
- Every 15 minutes for capacity alarms
- Daily Docker volume reconciliation
- Daily backup-target and last-success verification

## 7. Initial service resource limits

These are container ceilings for the controlled pilot. They are not reservations and should be tuned from measured workload evidence.

| Service | CPU limit | Memory limit | Storage rule |
|---|---:|---:|---|
| `sklegal-web` | 0.5 | 0.5 GiB | Static build and source on NFS |
| `sklegal-api` | 2 | 2 GiB | Bounded 2 GiB temporary allowance |
| `sklegal-worker-interactive` | 2 | 3 GiB | Bounded 2 GiB temporary allowance |
| `sklegal-worker-batch` | 4 | 6 GiB | Bounded 4 GiB temporary allowance |
| `sklegal-postgres` | 3 | 6 GiB | Local Docker volume, included in 10 GiB initial data budget |
| `sklegal-temporal` | 2 | 3 GiB | Persistence included in 6 GiB allowance |
| `sklegal-otel` | 0.5 | 1 GiB | Bounded 4 GiB log and telemetry allowance |
| Simulation connector workers | 0.5 | 1 GiB | No unbounded spool |

The summed pilot ceilings are 14.5 CPU and 22.5 GiB memory. The deployment-wide ceiling is 16 CPU and 24 GiB memory, leaving half the logical CPUs and more than 8 GiB of currently available memory outside the SKLegal ceiling. Batch concurrency must reduce when host available memory falls below 12 GiB and stop accepting new batch work below 8 GiB.

Storage budgets are monitored limits until a filesystem or container storage quota mechanism is separately implemented and tested.

## 8. Port-conflict qualification

Current TCP listeners include `22`, `53`, `111`, `631`, `3493`, `5432`, `7070`, `8384`, `8765`, `9050`, `9391`, `11434`, `11437`, `18782`, `18793`, `18794`, `18797`, `18800`, `22000`, `54193`, and `61577`.

The following initial SKLegal ports have no conflict in the captured snapshot:

| Service | Bind | Port |
|---|---|---:|
| OpenTelemetry gRPC | `127.0.0.1` | 4317 |
| OpenTelemetry HTTP | `127.0.0.1` | 4318 |
| Temporal frontend | `127.0.0.1` | 7233 |
| PostgreSQL | `127.0.0.1` | 15433 |
| API | `127.0.0.1` | 18080 |
| Web | `127.0.0.1` | 18081 |
| Temporal UI | `127.0.0.1` | 18088 |

All initial bindings are loopback-only. Reverse proxy ingress and any external listener require a separate deployment and security decision. The port scan must be repeated immediately before services start because listener state can change.

## 9. Backup targets and recovery placement

Observed candidate mounts:

| Target | Current availability | Status |
|---|---:|---|
| `/mnt/cloud/onedrive` | 1463.39 GiB | Mounted and project directory present |
| `/mnt/cloud/gdrive` | About 1.4 TiB | Mounted, candidate secondary copy |
| `/mnt/common` | About 1.4 TiB | Mounted, not selected for protected backups |
| `/mnt/scratch` | 100 GiB | Mounted, not a backup target |
| `/home/skuser01/backups` | Root filesystem | Existing but not a separate failure domain |

No SKLegal backup directory or scheduled SKLegal backup job exists. The only matching system timer was the operating system package database backup timer.

Initial placement plan:

1. Keep live PostgreSQL and Temporal persistence in dedicated local Docker volumes.
2. Export nightly encrypted logical backups to `/mnt/cloud/onedrive/projects/DAVE-AI/sklegal/backups/chiap01/` with 14 daily and 8 weekly generations.
3. Copy a weekly encrypted generation to `/mnt/cloud/gdrive/projects/DAVE-AI/sklegal/backups/chiap01/` after path ownership, cloud synchronization, encryption-key recovery, and retention are verified.
4. Store manifests, hashes, schema versions, and restore instructions beside encrypted backup objects. Never store raw secrets there.
5. Treat both NFS paths as the same local NAS failure domain until their independent cloud replication and recovery behavior is proven.
6. Use an initial pilot RPO of 24 hours and RTO of 8 hours. Sprint 5 performs the required restore qualification before rollout.

No backup directory was created and no write probe was performed during this read-only task.

## 10. Operational ownership

| Boundary | Current or proposed owner | Responsibility |
|---|---|---|
| Host, mounts, Docker, containerd, capacity alerts | `skuser01` as chiap01 platform owner | Change approval, host capacity, service recovery |
| Existing SKMemory database | SKMemory Compose project | Existing schema and volume, outside SKLegal ownership |
| Existing HammerTime model services | `skuser01` user services | Ports, models, and availability, outside SKLegal ownership |
| SKLegal repository and deployment definition | SKLegal maintainers, accountable owner `skuser01` | Versioned configuration and releases |
| SKLegal PostgreSQL and Temporal | Dedicated SKLegal services | Local live persistence, migrations, backup exports |
| NFS source and backup directories | `skuser01`, with storage service dependency at `10.0.0.111` | Access, retention, sync, and recovery verification |
| Capacity policy changes | Human-approved SKLegal platform task | Threshold and budget versioning |

Current host services are mixed between root-owned system units and `skuser01` user units. Docker and containerd have no systemd memory or CPU ceiling. Ollama runs as the `ollama` system user. The SKLegal Compose or systemd scaffold must encode its own limits and must not inherit unlimited defaults.

## 11. Executable verification evidence

Current fixture check:

```text
$ python3 scripts/check_capacity.py --policy config/platform/chiap01-capacity-policy.json --snapshot tests/fixtures/platform/chiap01-2026-08-19.json
host=chiap01
observed_at=2026-08-19T21:41:47-05:00
status=PASS
mount name=local-runtime status=PASS selected=/ used=54.0% available=400.88GiB reserve=40GiB after_reserve=360.88GiB
mount name=nfs-project status=PASS selected=/mnt/cloud/onedrive used=54.0% available=1463.39GiB reserve=150GiB after_reserve=1313.39GiB
ports status=PASS conflicts=none
docker-volumes status=PASS orphans=none missing=none
```

Unit tests:

```text
$ python3 -m unittest -v tests/test_capacity_policy.py
Ran 12 tests
OK
```

The tests cover current-pass behavior, used-percentage warning and critical states, free-space-floor warning and critical states while used percentage remains low, malformed mount input, snapshot schema validation, most-specific mount selection, port conflict, orphan Docker volume behavior, active reference to a missing Docker volume, and malformed Docker volume input. Warning and critical disk-pressure cases modify an in-memory fixture only. They do not allocate disk.

## 12. Read-only inspection commands

The principal commands were:

```text
df -hT
df -iT
df -B1 --output=source,fstype,size,used,avail,pcent,target / /mnt/cloud/onedrive
findmnt -R /mnt/cloud
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL
lscpu
free -h
docker system df -v
docker ps -a --no-trunc
docker inspect <observed-container-id>
docker volume ls
docker volume inspect <observed-volume-name>
ss -lntup
systemctl list-units --type=service --state=running
systemctl --user list-units --type=service --state=running
journalctl --disk-usage
du -x -h --max-depth=2 <observed-path>
docker exec skmem-pg pg_isready
docker exec skmem-pg psql -U postgres -Atc <read-only database-size query>
```

## 13. Known limitations and follow-up gates

- This is a point-in-time snapshot. It does not guarantee future port or capacity availability.
- NFS availability was observed, but latency, outage, sync completion, and restore behavior were not load-tested.
- Backup directories, jobs, encryption, retention, and restore tests are planned but not implemented by this task.
- Docker reclaimable space was not analyzed for rollback value and was not pruned.
- Log growth remains a host risk because `/var/log` is 29 GiB and `syslog.1` is about 17 GB. Capacity alarms are required even though current total space passes.
- The optional second NVMe was not mounted, formatted, or otherwise changed.
- Resource limits are the qualification baseline proposal for the scaffold task. Runtime tuning requires pilot telemetry.
- No application service was started or stopped.

## 14. Rollback impact

This task changed only repository documentation, a JSON policy, a captured JSON fixture, a dry-run checker, and tests. It made no host or data change. Rollback consists of removing those new repository files. There is no database, service, mount, Docker, corpus, or external-account rollback action.
