# Runbook: promote `.41` to control on loss of `.158`

**Epic:** `3bbf39ea`. **Cards:** `591d2b1a` (the runbook and drill),
`0afa9ffb` (a documented revert on every step). **Precondition sibling:**
`6bcf1e4c` verifies `.41` actually holds a current replica.
**Companions:** [control-unit-set.md](control-unit-set.md) (what `.41` gains,
unit by unit), [adr-node-role-model.md](adr-node-role-model.md) (why the SPOF
is accepted and this is its mitigation).

---

## Read this part first

You are here because `.158` is not answering. Three things are true and none
of them are obvious under stress:

**1. Nothing is dying while you read.** Losing the control seat does not stop
the fleet. Every service that was already converged keeps running. `sknoded`
on every node keeps reporting. Syncthing keeps replicating. What you have lost
is the ability to write a **new** spec, which means the fleet is frozen in its
current shape, not falling over. **You have hours, not minutes.** Slow down.

**2. The dangerous mistake is not being too slow, it is being too fast.** The
single-writer invariant is enforced by role, not by machine: `store.write_spec`
checks `writer.role != "operator"` and nothing else, and `skfleet`'s
`_operator()` claims that role on whatever box it is invoked from. `.41`
already has the `skfleet` CLI and already has a full copy of the fleet tree.
**`.41` can write specs today, without being promoted at all.** If `.158` is
alive but sick and you promote `.41` anyway, you get two operators, and
Syncthing will not error, it will pick a winner by timestamp and leave a
`.sync-conflict-` file that nobody reads. A spec you wrote will silently not
be the spec that is live.

**3. Promotion is reversible if you do it in the documented order and
irreversible if you improvise.** Every step below has a revert line. Use them.

If you only do one thing before touching anything: run **Step 0**, the freeze.
It is cheap, it is instantly reversible, and it stops the automation from
making decisions while you are making yours.

---

## The replica does NOT carry the agent signing keys. Read this before anything else.

Card `6bcf1e4c` verified the replica and found the one thing a promotion cannot
recover from on its own.

`.41` holds a genuinely current replica: all 18 sovereign source-of-truth classes
hash byte-for-byte identically, so a promoted `.41` inherits every memory, card,
seed, soul and coordination record. What it does NOT inherit is the ability to
sign as most of the agents that own them.

Measured 2026-08-16:

| node | private key files under `~/.skcapstone` | agents with `private.asc` |
|---|---|---|
| `.158` | 28 | architect, artisan, ava, coder, herald, jarvis, lumina, opus, scholar, sentinel, steward |
| `.41` | 4 | jarvis, lumina, opus |

**Eight agents' signing keys exist only on `.158`**: architect, artisan, ava, coder,
herald, scholar, sentinel, steward. Also absent from the replica:
`capauth/service/oidc_signing_key.pem` and the whole `skcomms/cot-pki` set (CA,
server, and five device keys). Roughly 25 files, 88KB.

This is not a bug and not a Syncthing failure. It is the `.stignore` rules
(`*.key`, `*.pem`, `**/private.*`) doing exactly their job: private key material
must never leave the node that owns it. The same three lines that keep 11 agent
keys off the GPU worker also keep 8 of them off the standby.

So the honest statement of the accepted SPOF is narrower than the ADR implies.
The mitigation covers STATE, not IDENTITY. A promoted `.41` is a working control
seat that cannot sign as eight of its agents until those keys are restored from
backup or the agents are re-keyed.

Two consequences for this runbook:

1. **Restoring the eight keys is a promotion step, not an afterthought.** It has
   to come from the sealed vault or from the `agents/*/backups` tarballs, not from
   Syncthing, which will never carry them.
2. **The operator key is not part of this problem.** `capauth/identity/` on `.158`
   holds `public.asc` only, so operator custody was never a Syncthing question and
   is not fixed or broken by a promotion.

If you are promoting under time pressure and the eight agents are not needed
immediately, promote first and restore keys after. Just do not believe the seat is
whole until they are back.

## Preconditions

Run all five. They are read-only. Write the answers down, on paper if you have
to, because you will want them again during fail-back.

### P1. Is `.41` reachable?

```
ssh cbrd21@100.86.156.5 hostname
```

Expect `cbrd21-laptop12thgenintelcore`. Note the address: `.41` is
**Tailscale-only**. The old `192.168.0.41` is dead and will hang rather than
refuse, which reads like a dead box when it is a dead route.

**If it fails:** you have lost both the control seat and the promotion target.
Do not proceed. This is a different incident. Bring `.41` up first, or bring
`.158` up, whichever is closer to possible.

### P2. Is `.41` holding a CURRENT replica? (card `6bcf1e4c`)

This is the precondition the whole runbook rests on. Promoting a node holding
a stale fleet tree means writing new specs on top of an old world.

```
ssh cbrd21@100.86.156.5 'ls -la ~/.skcapstone/fleet/objects/ ~/.skcapstone/fleet/objects/node/'
ssh cbrd21@100.86.156.5 'systemctl --user is-active syncthing.service'
ssh cbrd21@100.86.156.5 'syncthing cli show connections'
```

Three questions, in order of how much they tell you:

- **Does the tree exist and is it populated?** You want `objects/node/`,
  `objects/profile/`, `objects/service/`, `objects/cronjob/`,
  `objects/operatorapp/`, plus `_freeze.json` and `_protected.json`.
- **Is Syncthing running there?** If it is stopped, the replica is as old as
  the moment it stopped, and that could be days.
- **When did it last connect?** `syncthing cli show connections` gives
  per-device `connected` and `at`. A device that has not connected since
  before the incident is a replica frozen at that time.

If `.158` is still readable at all, compare generations directly. This is the
strongest check available:

```
# on .158
cat ~/.skcapstone/fleet/objects/node/node-41.json | grep -E '"generation"|"updatedAt"'
# on .41
ssh cbrd21@100.86.156.5 'grep -E "\"generation\"|\"updatedAt\"" ~/.skcapstone/fleet/objects/node/node-41.json'
```

Equal `generation` on both sides means the replica is current for that object.
Check two or three objects, not one.

**If the replica is stale:** do NOT promote yet. Start Syncthing on `.41`, or
wait for it to converge, and re-check. If `.158` is gone and the replica is
stale, you have a **data-loss decision, not a promotion decision**: read the
"Data-loss window" section below before you write anything, because the first
spec write on `.41` bumps `generation` and makes the divergence permanent.

**If `.41` has no fleet tree at all:** stop. Restore it from the most recent
`skcapstone-backup-gfs` artifact before proceeding. A promotion onto an empty
store is not a promotion, it is a new fleet.

### P3. Are the sync-conflict files already there?

```
find ~/.skcapstone/fleet -name '*.sync-conflict-*'
ssh cbrd21@100.86.156.5 "find ~/.skcapstone/fleet -name '*.sync-conflict-*'"
```

**Expect hits. As of 2026-08-16 there are two**, both under
`status/node-noroc2027/`, and both node objects report the condition
`SyncConflict / DoctorProbe` as `True`. That is pre-existing and it is not
your incident. Record the list **before** you promote so that after promotion
you can tell your conflicts from the old ones. A conflict file that appears
during promotion is the split-brain alarm, and you cannot hear an alarm you
cannot distinguish from the background.

### P4. What does the fleet think its own state is?

```
skfleet nodes
skfleet describe node node-41
skfleet describe node node-noroc2027
skfleet node doctor --all
```

Run these on whichever box answers. Record `skfleet nodes` output verbatim:
role, labels, capacity, and heartbeat age for every node. The heartbeat age is
your evidence for the Case A / Case B decision below.

**Known gap, do not be surprised by it:** `skfleet node doctor --all` prints
`skipped node-41: has published no inventory yet`, so there is no automated
drift check for `.41`. And `skfleet node doctor node-41` does **not** fix that:
with an explicit name it collects the **local** inventory and grades it
against `node-41`'s profile, which on `.158` means grading `.158`'s units as
if they were `.41`'s. It produces a confident, wrong answer. Use `--all`, read
the skip, and fall back to `systemctl --user list-unit-files --state=enabled`
over ssh.

### P5. Do you have what the missing units need?

Per [control-unit-set.md](control-unit-set.md), `.41` is missing **21 unit
files**, including `skoperator.timer` and `capauth-authz.service`, the two
that actually constitute the control seat. Several of the others need secrets
that are not on `.41`: Telegram bot tokens, TURN credentials, nostr relay
keys, TAK certificates.

```
skvault unlock --word rubikscube    # you will need this; confirm it works BEFORE you need it
```

**If skvault will not unlock:** you can still complete the control-plane
promotion (Phase 2 below), which needs no secrets. You cannot complete the
service restoration (Phase 3). Do Phase 2, then stop and solve skvault.

---

## Which case are you in?

This is the only branch in the runbook, and it changes the **order**, not the
steps. Getting it wrong in the safe direction costs you time. Getting it wrong
in the unsafe direction costs you the fleet store.

| | **Case A: `.158` is GONE** | **Case B: `.158` is ALIVE BUT DEGRADED** |
|---|---|---|
| looks like | no ping, no ssh, no heartbeat, and you know why (PSU, disk, theft, fire) | ssh is slow or flaky, some services are up, heartbeat is intermittent, disk full, OOM |
| the risk | data loss from an un-replicated write | **two operators writing the same store** |
| the order | promote `.41`, then worry about `.158` | **demote `.158` first**, then promote `.41` |

**When you are not sure, you are in Case B.** A box that might come back is a
box that will come back, at the worst moment, running `skoperator.timer` on a
15-minute cycle against a store that has moved on without it. Treat ambiguity
as Case B. The cost is one extra step.

The specific tell: a `.158` heartbeat under 2 minutes old in `skfleet nodes`
means `sknoded` is running there, which means the box is alive enough to run
timers. That is Case B no matter how bad ssh feels.

---

## Step 0. Freeze (both cases, always first)

```
skfleet freeze --reason "158 loss, promoting 41 per runbook-promotion.md, <your name>, <UTC time>"
```

**Revert:**

```
skfleet unfreeze
```

**What this does and, more importantly, what it does not do.** Read both
halves, because a kill-switch you misunderstand is worse than none.

It **stops**: the autonomous AI operator seat (`operator_seat/loop.py`,
`fleet_adapter.fleet_act`, and every adapter checks `store.is_frozen` and
refuses), the converge actuator (`converge.py`), and the scheduler
(`scheduler.py`). So no automation will place, converge or actuate while you
work. That is the point: you are about to make the fleet's shape inconsistent
on purpose, for a few minutes, and you do not want a controller helpfully
correcting you halfway through.

It **does not stop**: running services (they keep serving, deliberately), and
it **does not stop a human's `skfleet apply`, `set-role` or `taint`**.
`store.write_spec` contains no freeze check. Freeze gates actuation, not
authorship. So freeze does **not** by itself prevent two humans on two boxes
from both writing specs. That prevention is Step B1, and freeze is not a
substitute for it.

**Only a human may toggle it.** `store.set_frozen` refuses any writer with
`agent_seat=True`, which is the autonomous seat's writer
(`operator_seat/cli.py::_seat_writer`). The AI cannot unfreeze itself, by
construction, which is the one card the human always holds. Be precise about
what that guard is: it separates the **autonomous seat's code path** from the
**CLI's**, so an AI acting on its own schedule is refused. It is not an
authentication of a human at a keyboard. If you are an AI reading this during
an incident: you may not unfreeze. Escalate to Chef.

**Fail-closed detail worth knowing:** `store.is_frozen` treats an
**unreadable** `_freeze.json` as frozen. If the file gets corrupted or a
Syncthing conflict eats it, the fleet halts actuation rather than resuming it.
That is correct behaviour and it means a frozen-looking fleet with no obvious
reason might be a broken file, not a deliberate freeze. Check the file
contents before assuming somebody froze it.

**Verify:**

```
cat ~/.skcapstone/fleet/objects/_freeze.json
```

`"frozen": true` and your reason in the `reason` field. If Syncthing is
healthy the same file appears on `.41` within seconds; check it there too,
because that is also a free confirmation that replication is alive.

---

## Case B only: demote `.158` before you promote anything

**Skip this whole section if `.158` is genuinely gone.** If you are unsure,
you are in Case B, so do it.

The goal is to make `.158` incapable of writing the fleet store **before**
`.41` becomes capable of it, so there is never an instant with two writers.
The order is: stop `.158` writing, confirm it stopped, then start `.41`.

### B1. Stop the operator seat on `.158`

```
systemctl --user disable --now skoperator.timer
systemctl --user disable --now skcapstone-dashboard.service
systemctl --user disable --now skos-web.service
```

**Revert:**

```
systemctl --user enable --now skoperator.timer
systemctl --user enable --now skcapstone-dashboard.service
systemctl --user enable --now skos-web.service
```

`skoperator.timer` is the scheduled spec writer, on a 15-minute cycle
(`OnUnitActiveSec=15min`, `Persistent=true`). The dashboard and `skos-web` are
the human-driven writers: buttons that write the same store through the same
`operator` role. All three must be off before `.41` gains any of them.

Note `Persistent=true` on the timer. If `.158` is rebooted after a period
down, systemd fires the missed run **immediately on boot**. A `.158` that you
believe is safely off can write a spec within seconds of coming back. This is
why `disable` matters more than `stop`, and why fail-back has its own section.

**Verify, do not assume:**

```
systemctl --user is-enabled skoperator.timer      # expect: disabled
systemctl --user is-active  skoperator.timer      # expect: inactive
```

### B2. Stop the single-identity bridges on `.158`

Per [control-unit-set.md](control-unit-set.md), these hold a fleet-scoped
identity and cannot run in two places:

```
systemctl --user disable --now skchat-telegram-lumina.service
systemctl --user disable --now skchat-telegram-opus.service
systemctl --user disable --now skchat-nostr-relay.service
systemctl --user disable --now skcot.service
systemctl --user disable --now skcode-hostd.service
systemctl --user disable --now skcomms-api.service
systemctl --user disable --now skcomms-signaling-broker.service
systemctl --user disable --now skcomm-daemon.service
```

**Revert:** the same list with `enable --now`.

Defer this if `.158` is degraded but the bridges are working and you are not
yet ready to stand them up on `.41`. A working bridge on a sick box beats no
bridge anywhere. **But you may not enable the `.41` copy until the `.158` copy
is off.** Doing them one pair at a time, disable-then-enable, is fine and is
often the calmer path.

### B3. Stop the shared-destination timers on `.158`

```
systemctl --user disable --now capauth-backup.timer
systemctl --user disable --now skchat-backup.timer
systemctl --user disable --now skcomm-queue-drain.timer
systemctl --user disable --now skingest-maintain.timer
```

**Revert:** the same list with `enable --now`.

These write off-box. Two racing on one destination produces a half-written
archive that both runs believe they completed, and a queue drain that
double-delivers or double-claims. Nothing will complain if you forget these,
which is exactly why they are listed.

### B4. Leave these RUNNING on `.158`

Do not touch them. Turning them off is the mistake, not the fix:

- **`syncthing.service`** stays up. It is how `.41` learns anything. Stopping
  it is how you create a data-loss window rather than close one.
- **`sknoded.service`** stays up. `store.write_status` enforces
  `writer.node == node`, so `.158`'s noded can only write `.158`'s own status
  subtree. It cannot collide with `.41`'s. Its heartbeat is also how you will
  know when `.158` recovers.
- **`capauth-authz.service`** stays up. It is a stateless PDP on
  `127.0.0.1:8420`; every host that gates anything needs its own copy.
- **`skgateway.service`**: see Step 2.3. Do not disable it here.

### B5. Confirm the store has one writer

```
find ~/.skcapstone/fleet -name '*.sync-conflict-*'
```

Compare against your P3 baseline. **No new conflict files** means one writer.
New conflict files at this point mean something on `.158` is still writing and
you have not found it. Find it before continuing. Do not proceed on hope.

---

## Phase 2: promote `.41` (both cases)

Everything from here runs **on `.41`** unless it says otherwise.

```
ssh cbrd21@100.86.156.5
```

### Step 2.1. Re-verify the replica on `.41` itself

Do this again, on `.41`, even though you did P2. If Case B took a while, the
picture has moved.

```
ls ~/.skcapstone/fleet/objects/node/
cat ~/.skcapstone/fleet/objects/_freeze.json     # expect frozen: true, your reason
find ~/.skcapstone/fleet -name '*.sync-conflict-*'
```

Seeing **your own freeze reason** on `.41` is the single best evidence you
will get that replication is live in the direction you need. It is a message
you wrote, on `.158`, arriving here. If it is present, the replica is current
to within seconds.

**Revert:** none. Read-only.

**If your freeze reason is not there:** replication is broken or lagging.
Stop. Fix Syncthing before you write anything, or accept the data-loss
decision consciously (see below).

### Step 2.2. Bind the role

```
skfleet set-role node-41 control
```

**Revert:**

```
skfleet set-role node-41 builder-standby
```

This is the actual promotion as far as the fleet object model is concerned:
it writes `spec.role` on the node object, bumping its generation, going out
over Syncthing to every node. `set-role` overlays one field and rewrites
through `store.write_spec`, so `taints`, `cordoned`, `address` and `identity`
all round-trip untouched. Hand-editing the JSON would skip the generation bump
and the writer block; do not.

**Verify:**

```
skfleet describe node node-41 | grep -E '"role"|"generation"'
skfleet nodes
```

**A thing this step does NOT do:** it does not change
`spec.identity`. `node-41` carries `capauth:architect@skworld.io`, an `agent`
class identity, while `control` declares `capauthIdentityClass: operator`.
After `set-role` the node is bound to `control` while still presenting an
agent identity. **This is a known and accepted inconsistency for the duration
of an outage.** Nothing enforces the identity class at write time today, so it
does not block you. Do not try to fix it during the incident by editing
identities; that is a much larger change than a promotion and it is how a
one-hour outage becomes a one-day one. Record it and move on.

### Step 2.3. The gateway is an address handoff, not a systemd handoff

`skgateway.service` is **already enabled and active on `.41`**, on `:18780`,
in violation of `builder-standby`'s `units.mustNot`. That violation is
pre-existing (see [control-unit-set.md](control-unit-set.md), finding 1) and
today it works in your favour: there is nothing to enable.

Each gateway binds `:18780` on its own host, so two machines never fight for
the port. What they share is the config, out of Syncthing-replicated
`~/.skcapstone/gateway/skgateway.yaml`, and the clients, which resolve one
address.

**So the step is: repoint clients from `.158`'s gateway to `.41`'s.**

```
# on .41, confirm it is answering before you send anyone to it
curl -s http://127.0.0.1:18780/v1/models | head
systemctl --user is-active skgateway.service
```

**Revert:** repoint clients back to `.158`. Because both gateways stay up
throughout, this revert is instant and costs nothing. That is the one good
thing about the pre-existing violation.

Do not disable `skgateway.service` on `.158` in Case B. A gateway nobody is
pointed at is harmless, and leaving it up means the revert is a config change
rather than a service start.

### Step 2.4. Install the missing control units on `.41`

You need 21 unit files. **Do not install all 21.** Install the two that make
the seat, verify, and stop:

```
# on .41: capauth-authz.service, copied from .158's definition
systemctl --user cat capauth-authz.service    # on .158, to copy the ExecStart
# ExecStart=%h/.skenv/bin/capauth-service --host 127.0.0.1 --port 8420
systemctl --user daemon-reload
systemctl --user enable --now capauth-authz.service

# skcapstone.service: file already present on .41, just disabled
systemctl --user enable --now skcapstone.service
```

**Revert:**

```
systemctl --user disable --now capauth-authz.service
rm ~/.config/systemd/user/capauth-authz.service
systemctl --user daemon-reload
systemctl --user disable --now skcapstone.service
```

`skcapstone.service` is the cheapest win in this runbook: the unit file is
already on `.41` in `disabled` state, so it is one command with a one-command
revert and no file to write.

**Verify:**

```
systemctl --user is-active capauth-authz.service skcapstone.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8420/v1/authz/decide
```

### Step 2.5. `skoperator.timer` LAST, and only after `.158`'s is off

**This is the step that can break the fleet. Do not run it out of order.**

Stop and check, on `.158` if it is reachable:

```
systemctl --user is-enabled skoperator.timer     # MUST be: disabled
systemctl --user is-active  skoperator.timer     # MUST be: inactive
```

In Case A, where `.158` is genuinely gone, this check is satisfied by the box
being off. In Case B it is satisfied by Step B1 and by you having read the
output rather than assumed it.

Then, on `.41`, install the timer and its service (copy both from `.158`'s
definitions; the timer is `OnBootSec=2min`, `OnUnitActiveSec=15min`,
`Persistent=true`) and:

```
systemctl --user daemon-reload
systemctl --user enable --now skoperator.timer
```

**Revert:**

```
systemctl --user disable --now skoperator.timer
rm ~/.config/systemd/user/skoperator.timer
systemctl --user daemon-reload
```

The revert is clean and it is the one you will use during fail-back. Removing
the unit file, not just disabling it, matters: `Persistent=true` means a
lingering enabled timer fires a missed run on the next boot, and `.41` is a
laptop that boots often.

**Verify:**

```
systemctl --user list-timers skoperator.timer
find ~/.skcapstone/fleet -name '*.sync-conflict-*'    # still only your P3 baseline
```

Wait one full timer cycle, 15 minutes, and re-run the conflict check. A new
conflict file here means two operators. If you see one: immediately
`systemctl --user disable --now skoperator.timer` on `.41`, and find the other
writer before doing anything else.

### Step 2.6. Unfreeze

```
skfleet unfreeze
```

**Revert:**

```
skfleet freeze --reason "backing out promotion"
```

Only now. Unfreezing lets convergence and the AI seat act again, and you want
them acting against a fleet with exactly one operator, not a fleet mid-handoff.

**Verify:**

```
skfleet nodes
skfleet node doctor --all
skfleet get cronjobs
```

`skfleet nodes` should show `node-41` with `role=control`. Heartbeats should
be fresh. In Case B, `.158` will still appear as `Ready`, which is correct and
expected: it is alive, it is just not the seat any more.

### Step 2.7. Restore the user-facing services (not urgent)

The 19 remaining absent units are chat, comms, voice and backup surfaces. They
are outages people notice, but they are not control-plane outages, and the
control plane is now working. Do them one at a time, each one
disable-on-`.158`-then-enable-on-`.41`, needing skvault for the tokens and
credentials. [control-unit-set.md](control-unit-set.md) has the per-unit
detail, ports and which ones are strict handoff.

**Revert, per unit:** disable on `.41`, remove the unit file, re-enable on
`.158`.

Stop for the night after Step 2.6 if you can. The remaining work is better
done rested, and each unit's revert is independent.

---

## How two control seats are actually prevented

Not by the code. Be clear-eyed about this, because a runbook that overstates
its safety net gets people hurt.

**What the code does enforce.** `store.write_spec` refuses any writer whose
role is not `operator`. `store.write_status` refuses a writer whose `node`
does not match the node it is writing status for, so `sknoded` is genuinely
safe in parallel. `store.set_frozen` and `store.write_plane_file` refuse any
writer with `agent_seat=True`, so the autonomous seat can neither unfreeze
itself nor edit the carve-out manifest listing its own guardrails.

**What the code cannot enforce.** `skfleet`'s `_operator()` constructs
`Writer(role="operator", node=self_node_name(), ...)` on any machine it runs
on, with no check against the fleet's own record of which node holds the
`control` role. Two machines both presenting `operator` both pass. The store
sees one role, not two boxes. And the transport underneath is Syncthing, which
does not lock, does not order, and resolves divergence by writing a
`.sync-conflict-` file and keeping one version.

**So the prevention is procedural, and these four are it:**

1. **Order.** Case B demotes `.158` (B1) before `.41` gains anything (2.5).
   There is no window with both enabled because the sequence does not contain
   one. This is the whole mechanism. Everything else is detection.
2. **`skoperator.timer` is the last thing enabled and the first thing
   disabled.** It is the only scheduled writer, so bracketing the promotion
   with it means the risky window is minutes of deliberate work, not hours of
   background timers.
3. **Conflict-file detection, with a baseline.** P3 records the pre-existing
   conflicts so a new one is visible. Steps B5, 2.5 and 2.6 re-check. This is
   how you find out you were wrong, and it works because the alarm and the
   damage are the same artifact.
4. **The freeze, for everything except human authorship.** It stops the AI
   seat and the actuators cold. It does not stop `skfleet apply` from a
   terminal. Do not lean on it for what it does not do.

There is a fifth thing that is not a mechanism but is worth saying out loud:
**only one person promotes.** Two humans in two terminals is the same
split-brain with a slower clock. Say in the incident channel who is driving.

---

## The data-loss window, honestly

Syncthing is eventually consistent. There is no synchronous commit anywhere in
this design. So there is a window, and pretending otherwise helps nobody.

**What can be lost.** Any spec written on `.158` that had not replicated to
`.41` when `.158` stopped. In practice: object specs (`objects/**`) written by
`skoperator.timer` or by a human in the last sync interval, and status writes
from `.158`'s own `sknoded` in the same window.

**How big the window is.** Small, and here is why you can believe that. The
fleet tree is **368K** total ([control-bus-folder.md](control-bus-folder.md)),
individual objects are 1KB to 8KB, and Syncthing's default rescan on a small
folder with connected peers is seconds, not minutes. The realistic worst case
is **one Syncthing sync interval, seconds to a couple of minutes**, and only
for writes that happened inside it.

The exception, and it is the one that actually bites: **if Syncthing on `.41`
was already stopped or disconnected before the incident, the window is however
long it was disconnected.** That could be days. This is exactly what P2 is
for, and it is why `syncthing cli show connections` and its `at` timestamp are
in the preconditions rather than buried here.

**How to bound it, before you write anything.**

```
# on .41, the newest thing the replica knows about
find ~/.skcapstone/fleet/objects -name '*.json' -printf '%T@ %p\n' | sort -rn | head -5
```

The newest `updatedAt` in that set is your replica's horizon. Anything `.158`
wrote after it is at risk. If `.158`'s disk is readable at all, even from a
rescue boot or by pulling the disk, copy `~/.skcapstone/fleet/objects/`
off it **before** promoting and diff the two trees. Ten minutes of diffing
beats a week of wondering which spec went missing.

**What to do when you cannot bound it.** Promote anyway, and treat every spec
as suspect until re-verified. The fleet keeps running its converged shape
regardless, so the loss is of recent *intent*, not of running services.
Re-assert the specs you care about by hand. Deliberately re-writing a spec you
already wrote is cheap. Discovering three weeks later that a cronjob was
silently disabled is not.

**What is NOT at risk.** Running services on other nodes. Node status subtrees
(each node owns and rewrites its own). Anything already converged. The loss is
confined to recent writes to `objects/`, which is the smallest and most
re-creatable part of the system. That is not an accident; it is what the
368K-versus-19G folder split is for.

---

## Failing back to `.158`

**This is the step people forget, and it is the one that leaves two seats
live.** A `.158` that comes back is a `.158` running its old configuration,
which believed it was the control seat. `skoperator.timer` has
`Persistent=true`: after a period down, systemd fires the missed run
**immediately at boot**. If you did Case A and never demoted `.158` (because
it was gone, so why would you), the seat re-arms itself the moment the box
powers on, and you get two operators with nobody at a keyboard.

**Therefore: the fail-back sequence starts BEFORE `.158` is on the network.**

### F0. Freeze first

```
skfleet freeze --reason "158 returning, failing back per runbook-promotion.md"
```

**Revert:** `skfleet unfreeze`.

### F1. Bring `.158` up with no network, or with the SK units already off

If you can boot it single-user, offline, or with networking down, do that and
disable the writers before it ever reaches the tailnet:

```
systemctl --user disable skoperator.timer
systemctl --user disable skcapstone-dashboard.service
systemctl --user disable skos-web.service
```

**Revert:** `enable` the same three. That revert is Step F5, so this is not a
step you can skip and fix later.

**If you cannot boot it offline** (Proxmox console unavailable, remote hands,
no IPMI): boot it, then immediately ssh in and run the disables. Accept that
`skoperator` may fire once during that gap. Check for conflict files
afterwards and expect to find one. Knowing you took the risk beats discovering
it.

**In Case A this is where you catch the trap.** Case A never ran Step B1,
because there was nothing to run it against. F1 is Case A's B1, delayed. Do
not skip it because "we never promoted `.158` back".

### F2. Let Syncthing converge, and read the result

Bring `.158` onto the network with the writers disabled. Let Syncthing settle.

```
syncthing cli show connections
find ~/.skcapstone/fleet -name '*.sync-conflict-*'
```

`.158` will pull the specs `.41` wrote while it was the seat, including
`node-41.json` with `role: control`. Compare the conflict list against your P3
baseline. Resolve any new conflict **by hand**, choosing the version `.41`
wrote, because `.41` was the authoritative seat for that period. Then delete
the conflict file so the next incident's baseline is clean.

**Revert:** none. This step only reads and reconciles.

### F3. Demote `.41`

**On `.41`, and before `.158` gains anything:**

```
systemctl --user disable --now skoperator.timer
rm ~/.config/systemd/user/skoperator.timer
systemctl --user daemon-reload
skfleet set-role node-41 builder-standby
```

**Revert:**

```
skfleet set-role node-41 control
# reinstall skoperator.timer per Step 2.5
```

Same rule as promotion, mirrored: the seat is off on the outgoing box before
it is on on the incoming one. Remove the unit file, do not merely disable it,
for the `Persistent=true` reason.

Also reverse Step 2.4 and any of 2.7 you did:

```
systemctl --user disable --now capauth-authz.service skcapstone.service
```

Leave `skgateway.service` and `sknoded.service` and `syncthing.service`
running on `.41`. The gateway was there before the incident and removing it is
a separate change; the other two are parallel-safe and required.

**Verify:**

```
systemctl --user is-enabled skoperator.timer     # on .41, expect: not-found or disabled
skfleet describe node node-41 | grep '"role"'    # expect: builder-standby
```

### F4. Re-verify `.158` before handing the seat back

```
skfleet node doctor --all
skfleet nodes
df -h ~/.skcapstone
```

If `.158` came back degraded rather than fixed, **do not hand the seat back
yet**. A seat on a box that is about to fail again is a second promotion in
your near future, at a worse hour. `.158` reports 6.9GB free disk and a
`MemoryPressure` reason of "4.1GB available" in normal operation, so it does
not have much headroom to lose. Check that the thing that killed it is
actually fixed.

**Revert:** none. Read-only. Staying on `.41` is a valid outcome, and one you
should be willing to choose.

### F5. Re-enable the seat on `.158`

```
systemctl --user enable --now skoperator.timer
systemctl --user enable --now skcapstone-dashboard.service
systemctl --user enable --now skos-web.service
```

Then re-enable whatever B2 and B3 turned off, and repoint gateway clients back
to `.158` per 2.3's revert.

**Revert:** disable the same list; `.41` is still capable of retaking the seat
via Step 2.5.

`node-noroc2027`'s object still carries `role: control` throughout, since
nothing in this runbook changes it, so there is nothing to set back. That is
deliberate: the fewer spec writes fail-back needs, the fewer chances it has to
conflict.

### F6. Unfreeze and confirm exactly one seat

```
skfleet unfreeze
skfleet nodes
skfleet node doctor --all
find ~/.skcapstone/fleet -name '*.sync-conflict-*'
ssh cbrd21@100.86.156.5 "find ~/.skcapstone/fleet -name '*.sync-conflict-*'"
```

Then the check that actually closes the incident, on **both** boxes:

```
systemctl --user is-enabled skoperator.timer
```

`.158`: `enabled`. `.41`: `disabled` or `not-found`. **One seat.** Write that
pair of outputs into the incident record. It is the only evidence that the
fail-back finished, as opposed to appearing to.

Wait one full 15-minute timer cycle and re-check the conflict lists one last
time before you close.

---

## Drilling this

Per card `591d2b1a` the drill runs **against a scratch fleet store, never
against production**. `paths.default_paths()` honours `SKFLEET_ROOT`:

```
export SKFLEET_ROOT=/tmp/drill-fleet
cp -r ~/.skcapstone/fleet/* /tmp/drill-fleet/
skfleet nodes
skfleet set-role node-41 control
skfleet freeze --reason drill
```

Every `skfleet` verb in this runbook then operates on the copy. The systemd
steps have no such override and must be reasoned about rather than executed,
or executed on a scratch user account. **Until this has actually been run, the
mitigation is a plan and not a capability**, which is the caveat the ADR
attaches to its own acceptance of the SPOF.

The most valuable thing to drill is not the happy path. It is P2 failing:
practise deciding, with an incomplete replica in front of you, whether to
promote and eat the loss or wait and eat the downtime. That decision is the
hard part, and it is the one you do not want to be making for the first time.

---

## Command index

Every command in this runbook was verified to exist on 2026-08-16 by running
its `--help`. Nothing below is plausible-looking invention.

| command | verified | effect |
|---|---|---|
| `skfleet nodes` | yes | read-only |
| `skfleet describe <kind> <name>` | yes | read-only |
| `skfleet node doctor [NAME] [--all] [--json] [--strict]` | yes | read-only, self-documented as report-only |
| `skfleet control-bus audit` | yes | read-only, self-documented as safe on any node |
| `skfleet get <cronjobs\|modelservers\|agents\|configs>` | yes | read-only |
| `skfleet placements`, `skfleet services` | yes | read-only |
| `skfleet freeze [--reason TEXT]` | yes | **writes** `objects/_freeze.json`, human-only |
| `skfleet unfreeze` | yes | **writes** `objects/_freeze.json`, human-only |
| `skfleet set-role <name> <role>` | yes | **writes** the node spec, bumps generation |
| `skfleet taint <name> KEY=VALUE:EFFECT` | yes (wave 3) | **writes** the node spec |
| `skfleet untaint <name> KEY` | yes (wave 3) | **writes** the node spec; absent key is a success |
| `skfleet cordon <name>` / `uncordon <name>` | yes | **writes** the node spec |
| `skfleet actuation <name> --enable/--disable` | yes | **writes** the node spec |
| `skfleet apply -f FILE` | yes | **writes** an object spec |
| `syncthing cli show connections` | yes | read-only |
| `syncthing cli show system` | yes | read-only |
| `skcapstone doctor [--fix] [--verbose]` | yes | read-only without `--fix` |
| `skvault unlock --word <word>` | yes | unlocks the vault |

`skfleet taint` and `skfleet untaint` are not used in the main sequence, and
that is on purpose: taints steer the **scheduler**, not the control seat, and
there is no `NoExecute` effect in this fleet
([travel-taint-runbook.md](travel-taint-runbook.md)), so tainting `.158`
during an incident moves no running work and stops nothing you needed stopped.
They are listed because reaching for them is a natural instinct here and
because knowing they will not help is worth more than the instinct. If you do
want `.158` to stop attracting new placements while you work:

```
skfleet taint node-noroc2027 outage=true:NoSchedule
skfleet untaint node-noroc2027 outage
```

Both are write-on-change and idempotent on the key, so the untaint is safe to
run unconditionally during fail-back.
