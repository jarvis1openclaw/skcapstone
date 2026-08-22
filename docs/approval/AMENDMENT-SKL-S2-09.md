# Proposed amendment: HammerTime provenance path for the Liberty Auto pilot

Amendment ID: `AMENDMENT-SKL-S2-09`
Card: `a4fcdd8e` (`SKL-S2-09`)
Recorded timestamp: 2026-08-21T18:19:10-05:00
Prepared by: `kimi-skl-s2-09`
Human owner: `skuser01`
Status: proposed (pending human approval)

This record proposes a scoped amendment. It does not modify any hash-pinned
document. The human owner approves, applies the single-line edit, and re-pins
`docs/approval/DESIGN-HASHES.sha256` separately.

## Problem

`docs/architecture/LIBERTY-AUTO-PILOT-TDD.md` line 7 pins the pilot HammerTime
source to a path containing a space:

```text
Source path: `/mnt/cloud/onedrive/projects/DAVE AI/hammerTime/incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order`
```

The SKLegal repository and the HammerTime workspace now live under
`/mnt/cloud/onedrive/projects/DAVE-AI`. Because the pilot TDD is hash-pinned,
the stale path cannot be corrected silently; it requires this amendment.

## Canonical-root determination and evidence

The canonical HammerTime root is
`/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime`. Evidence collected
read-only on 2026-08-21:

```text
ls -ld "/mnt/cloud/onedrive/projects/DAVE AI" "/mnt/cloud/onedrive/projects/DAVE-AI"
Result:
lrwxrwxrwx  1 skuser01 skuser01  7 Aug 19 21:27 /mnt/cloud/onedrive/projects/DAVE AI -> DAVE-AI
drwxrwsr-x 16 skuser01 skuser01 25 Aug 21 13:32 /mnt/cloud/onedrive/projects/DAVE-AI

readlink -f "/mnt/cloud/onedrive/projects/DAVE AI/hammerTime"
Result: /mnt/cloud/onedrive/projects/DAVE-AI/hammerTime

ls -ld "/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order"
Result:
drwxrwsr-x 3 mrarch skuser01 5 Aug 16 20:13 /mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order

rg -n "DAVE AI" /mnt/cloud/onedrive/projects/DAVE-AI/sklegal
Result: exactly one match, docs/architecture/LIBERTY-AUTO-PILOT-TDD.md line 7.
```

Interpretation:

- `DAVE AI` is a symbolic link to the real directory `DAVE-AI`, created
  2026-08-19 21:27. Both spellings resolve to the same inode, so the pinned
  path was never a distinct second tree; there is no conflicting fork to
  reconcile.
- The real, canonical directory is `DAVE-AI` (no space). The space spelling
  exists only as a compatibility symlink.
- The pilot matter directory for `liberty-auto-plaza-libertyville-nissan-purchase-order`
  exists and resolves identically under the canonical root.
- Only one document in the SKLegal repository references the space spelling,
  so the amendment is exactly one line in one file.
- Per the repository vocabulary rule, the legacy space path is preserved as a
  provenance alias in this record, not as a canonical reference.

No HammerTime `Inbox/` content was searched, read, moved, or processed. No
HammerTime path was written.

## Proposed change

In `docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`, replace line 7:

```text
Source path: `/mnt/cloud/onedrive/projects/DAVE AI/hammerTime/incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order`
```

with:

```text
Source path: `/mnt/cloud/onedrive/projects/DAVE-AI/hammerTime/incidents/problems/liberty-auto-plaza-libertyville-nissan-purchase-order`
```

No other line of the document changes. The status line of the pilot TDD may
additionally note `provenance path amended 2026-08-21` at the owner's
discretion; if added, the re-pinned hash must be recomputed after that edit.

## Hash evidence

Current pinned hash, verified against the working tree before this amendment
was prepared:

```text
sha256sum docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
8a30c43f66bc70b9a104beb04686e444e0144c6b9e0842b8239865ae5fcbab52  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
```

This matches line 2 of `docs/approval/DESIGN-HASHES.sha256`, confirming the
document was not silently edited.

Proposed post-amendment hash (single-line substitution above, nothing else),
pre-computed so the owner can verify the edit mechanically:

```text
5d3ab98296173983d771298f81698ef46eb612c723992dbd0956908ed4343c64  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
```

Proposed replacement line for `docs/approval/DESIGN-HASHES.sha256` upon
approval:

```text
5d3ab98296173983d771298f81698ef46eb612c723992dbd0956908ed4343c64  docs/architecture/LIBERTY-AUTO-PILOT-TDD.md
```

All other pinned hashes remain unchanged. Historical hashes in
`ARCHITECTURE-APPROVAL.md` and `AMENDMENT-SKL-S2-10.md` are not rewritten.

## Preserved boundaries

This amendment does not change:

- HammerTime custody of original corpus artifacts and promoted releases;
- the Liberty Auto Plaza pilot scope, source matter `PRB-2026-009`, or source
  event `INC-016`;
- the AMENDMENT-SKL-S2-10 local PostgreSQL retrieval architecture;
- CapAuth, Tenant, Matter, conflict, privilege, ethical-wall, retention, and
  legal-hold enforcement;
- simulation-only external actions and connector activation gates;
- the prohibition on HammerTime `Inbox/` processing, production deployment,
  additional Matter migration, and outbound legal actions without their own
  eligible cards and gates.

## Approval effect

Upon human approval:

- Apply the single-line path substitution to
  `docs/architecture/LIBERTY-AUTO-PILOT-TDD.md`.
- Update the `LIBERTY-AUTO-PILOT-TDD.md` line in
  `docs/approval/DESIGN-HASHES.sha256` to the proposed hash above, recomputed
  at apply time if the owner also edits the status line.
- Update `tests/test_amendment_skl_s2_09_proposal.py`, which asserts the
  pre-approval state (status proposed and the current pinned hash), to match
  the approved state.
- Set this record's status to approved with the owner's decision recorded.

Rollback is the reverse of the same single-line edit and hash restore.
