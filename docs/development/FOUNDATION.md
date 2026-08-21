# SKLegal engineering foundation

## Prerequisites

- Python 3.12 on Linux x86_64
- Node 22 and npm 10
- Docker Engine with Docker Compose 2
- GNU Make

No global Python or Node package installation is required. `make bootstrap`
verifies and extracts the pinned official `uv` release archive into `.tools`,
synchronizes the exact Python dependency graph from `uv.lock` into `.venv`, and
installs Node packages
from `package-lock.json`. Both directories are ignored and remain on the NFS
project path.

Bootstrap validates a digest of both Node manifests and the lockfile before it
reuses `node_modules`. A required reinstall uses `npm ci --prefer-offline` with
a five-minute timeout so a filesystem stall fails visibly instead of hanging
CI. Fresh Python synchronization has a ten-minute timeout for the same reason.
Every bootstrap timeout sends `TERM` at the bound and `KILL` 30 seconds later.
The exact Python lock check and npm offline, lifecycle-script, funding, and
audit flags remain unchanged.

## Commands

- `make bootstrap` creates a reproducible project-local toolchain.
- `make dev-deps` starts PostgreSQL and Temporal with loopback-only ports.
- `make dev-deps-check` validates Compose without starting services.
- `make dev-deps-down` removes development containers and their test volume.
- `make check` bootstraps and runs every required check.
- `make clean-room-check` copies only Git-eligible source files into a temporary
  directory under validated local scratch, then runs `make check` there. The
  default scratch root is exactly `/run/user/$uid` when that directory is
  canonical, private, and owned by the current user, otherwise `/tmp`. It never
  stages under the repository, a synced parent, or an arbitrary
  `XDG_RUNTIME_DIR`. An explicit `SKLEGAL_CLEAN_ROOM_ROOT` override must name an
  exact trusted anchor, either `/tmp` or canonical private
  `/run/user/$uid`. Renameable descendant overrides, links, path escapes,
  repository overlap, and other roots are rejected.

The clean-room copy is a clean-checkout-equivalent test. The source allowlist is
exactly `git ls-files --cached --others --exclude-standard`; command-scope Git
configuration disables local
filesystem monitors, hooks, untracked cache, and index preloading. Git runs
through a held repository descriptor with optional locks disabled. That same
descriptor and exact device and inode identity remain bound through copy, so a
repository path swap fails closed. Generated outputs are denied. Source and
destination traversal uses directory descriptors with `O_NOFOLLOW`. Files
must be regular and singly linked, source device and inode metadata must remain
stable, and destination creation is exclusive. Every copied byte and SHA-256
digest is verified before the nested gate starts. A copy closes and unlinks its
exact newly created destination in its own exception path. Broker-private
staging repeats the same immediate cleanup after any post-copy open, identity,
type, or size failure, and records only files and bytes that actually reached
its descriptor ledger. Existing canonical,
user-owned uv and npm content cache roots are shared by path. Cache links and
generated cache or build paths are rejected. `.tools`, `.venv`,
`node_modules`, `build`, and other build outputs are not copied.

The nested gate receives a closed allowlist environment through `/usr/bin/env
-i`; ambient credentials, proxy settings, language startup variables, user
configuration, and inherited make or Node options do not cross the boundary.
Launcher, control, Git, environment, Python, sleep, and make executables are
absolute and validated before use. The controller itself receives only the
user-bus address, exact runtime directory, and locale.

The nested `make check` runs inside a transient systemd user service configured
with `ExitType=cgroup` and `KillMode=control-group`. Qualification fails closed
when that OS-backed containment boundary is unavailable or the exact expected
cgroup cannot be observed. This owns descendants that call `setsid` or
double-fork. The harness computes and registers a provisional unit and cgroup
handle before it calls `systemd-run`. It blocks `SIGINT` and `SIGTERM` from
registration through process assignment and binds the returned process to the
registered slot before restoring the signal mask. On an exception or outage,
the harness escalates from controller `TERM` to `KILL` and the bound or existing
candidate cgroup's kernel `cgroup.kill` file, then proves the observed cgroup
empty or absent. An unobserved candidate path is never accepted as extinction
proof.

For the complete parent invocation, a temporary `SIGTERM` handler converts the
signal to controlled cancellation. A signal pending during the blocked launch
window is delivered only after the process has been assigned to its registered
slot, then enters the same outer containment, broker, workspace cleanup, and
terminal receipt path. Signals received during cleanup are recorded without
interrupting cleanup. The original handler is restored only after those owned
resources have been extinguished or their cleanup failure has been recorded.

The service also declares bind, read-only parent, and exact writable-path
properties. User-systemd on the qualification host records but does not enact
those mount properties, so they are not treated as the effective rename
boundary. The nested Python launcher therefore requires Linux Landlock ABI 5
or newer and installs a kernel ruleset before `make`: source is readable, but
execution is allowed only for a curated set of absolute bootstrap and check
executables plus files created inside the exact copied workspace. Direct
Docker, GPG, gpg-agent control, systemctl, and systemd-run executables are not
allowed. System reads are limited to `/usr`, `/etc`, `/proc`, `/sys`, the
system resolver directory, and exact random devices. In particular, the
payload cannot list or read the parent-owned broker-private state under
`/tmp`. Writes are allowed only in the copied workspace, validated uv and npm
caches, and `/dev/null`. Moving the workspace or scratch anchor needs a parent
directory right that is never granted. Qualification fails closed when
Landlock is unavailable.

The transient service also applies
`RestrictAddressFamilies=AF_INET AF_INET6`. The checked process therefore
cannot create an `AF_UNIX` socket to the user systemd bus, Docker daemon, GPG
agent, or another host Unix service. Real host tests prove raw Docker socket
connection fails and nested systemd service creation cannot escape the
registered cgroup.

The ruleset still permits bootstrap-shaped writes inside `.tools`,
`node_modules`, and the exact shared uv and npm caches. The parent harness
creates per-run loopback Docker and GPG brokers and installs only `docker` and
`gpg` shims in the copied workspace's `.clean-bin`. A random per-run credential
authenticates their bounded protocol and is never included in the terminal
receipt. The Docker broker accepts only the repository's exact Compose
configuration and status forms plus a pinned, labeled, tmpfs-backed disposable
PostgreSQL container with `--network none`, exact readiness and psql commands,
and removal or inspection of a container already in its ledger. It denies
arbitrary images, mounts, networks, exec programs, containers, and Compose
paths. Before the payload starts, the broker copies the exact inventory Compose
file through held source and destination descriptors into a broker-owned local
temporary directory. Docker receives only that identity-checked private
snapshot and a private working directory, never the mutable payload path.

The GPG broker accepts only an isolated logical synthetic keyring under the
workspace, exact test-key generation and listing, detached signing, and
verification. On first use, it validates the logical directory through the
held workspace descriptor and maps it to a newly created, descriptor-held
physical keyring in the broker-private directory. Every later operation and
gpg-agent cleanup uses only that immutable physical mapping. Verification
signature and payload files are copied through descriptors into private
request snapshots before unrestricted GPG runs. Parent execution and cleanup
never receive a payload-controlled home, input, or working-directory path.
Each verification snapshot is descriptor-removed in the command's `finally`
path, including errors, and a broker session may create only one private
keyring and agent.

Both loopback brokers share one fixed resource budget. Broker construction
creates no thread. The parent first registers the provisional broker in its
cleanup ledger, then blocks `SIGINT` and `SIGTERM` while each endpoint starts
exactly four fixed workers and one server thread. A partial start joins every
thread it created, and cleanup is not reported until the registered thread set
is empty. Each endpoint has an eight-connection queue. No connection can
create or remain as a retained thread, and the shared budget permits at most
four active request workers across the endpoints. A session also permits two
concurrent external commands and 1,536 accepted requests. The wire boundary
reserves capacity before reading a declared body or allocating it, then counts
every byte returned by `recv` or `send`, including four-byte headers and
malformed, truncated, denied, and error frames. Cumulative received wire bytes
are limited to 16 MiB and sent wire bytes to 8 MiB. An individual frame is
limited to 8 MiB, standard input and staged files to 4 MiB each, and combined
command output to 4 MiB. External stdout, stderr, and stdin are serviced
concurrently through nonblocking pipes. Output is counted as it is read and is
limited to 4 MiB per command and 8 MiB cumulatively. Crossing either limit
immediately clears the bounded capture, escalates the command process group
from `TERM` to `KILL` when needed, and returns only sanitized status 126.
Attempts, complete requests, connection denials, request denials, output bytes
and denials, worker peaks, staged file and byte creation, removal, current, and
peak state are reported as sanitized integers. The Docker broker may create
at most one PostgreSQL container in the session, including after failure or
removal. Budget denial is bounded and sanitized and cannot start new parent
work.

The broker adds exact host limits to that one container: 2 CPUs, 1 GiB memory
and swap, 256 PIDs, 1,024 file descriptors, and a 30-second stop timeout. The
pinned image starts through BusyBox `/usr/bin/timeout -s TERM -k 30 900`, so it
self-terminates after 15 minutes even if the parent disappears. Container
labels and the immutable cleanup ledger still bind final removal and readback.
The limits were sized against the complete 40-contract broker-backed replay,
which used 966 connections and complete requests, 4,062,671 received wire
bytes, 619,180 sent wire bytes, 422,806 streamed external-output bytes, a peak
of two workers, and a peak of two external commands. It created five
descriptor-staged files totaling 3,983 bytes. Immediate snapshot and final
Compose cleanup removed all five and returned both current counters to zero.

Broker shutdown terminates active command process groups, removes every
ledgered container, kills each synthetic gpg-agent, verifies that no tracked
socket or service remains, closes every private descriptor, and removes the
exact broker-private directory identity. A path swap or any cleanup failure
makes the receipt fail closed without following the replacement target.

The harness emits flushed JSON phase events with elapsed time. At 15 minutes
it sends `TERM` to the complete cgroup, waits 15 seconds, sends `KILL` when any
residual population remains, and performs a final bounded wait. Every terminal
path verifies that the unit is inactive and its observed cgroup is absent or
empty.

The temporary tree remains bound to held parent and directory descriptors.
Both parent and target device and inode identities must remain stable. Rename
or replacement is detected, cleanup is applied only to the exact created
directory identity, and no passing receipt can report a remnant. The
last line is a machine-readable `sklegal-clean-room-receipt/v6` JSON receipt
with normalized exit status, source inventory digest, elapsed time, temporary
identity and cleanup state, containment unit and extinction state, and an
external-broker cleanup result. It also includes only sanitized broker resource
counts. The random broker credential is never included.

The complete check covers approved design hashes, Compose validation, Python
and frontend format, lint and type checks, unit and integration tests,
migration manifest validation, fixture safety, reproducible SBOM generation, secret
scanning, and Python and Node vulnerability audits. Migration and provisioning
scripts are included in static type checks.

All tests use synthetic or platform-only fixtures. Persistence integration
tests create an isolated PostgreSQL container with tmpfs storage, no published
port, no named volume, and automatic cleanup. Tests do not access `skmem-pg`,
the HammerTime corpus or Inbox, external accounts, or protected matter content.
