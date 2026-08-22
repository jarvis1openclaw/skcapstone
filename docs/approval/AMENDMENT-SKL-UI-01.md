# Amendment record: UI-first execution order

Amendment ID: `AMENDMENT-SKL-UI-01`  
Card: `6775be53` (`SKL-UI-01`)  
Recorded by: `jarvis`  
Status: approved for execution

## Owner decision

On 2026-08-21, the human owner directed SKLegal to review and reorder all
sprints so delivery focuses first on deploying a web UI that demonstrates
application progress, then authorized execution.

## Effect

The governing execution overlay is
`docs/planning/UI-FIRST-EXECUTION-PLAN.md`. It pulls a read-only progress UI
and the first useful legal workspace ahead of the remaining agent and
connector depth.

This is an execution-order amendment. It does not rename stable task keys,
remove dependencies, change the legal-domain architecture, or modify a
hash-pinned approved document. The original sprint plan remains immutable
approval evidence.

## Preserved gates

- Only eligible, explicitly claimed leaf cards may change the repository.
- The first deployed surface is loopback-only and contains project status or
  synthetic demonstration data.
- Protected Client and Matter routes remain fail closed.
- Production application activation requires its own eligible task and gates.
- HammerTime `Inbox/` processing, additional Matter migration, external
  account creation, and outbound legal actions are not authorized.
- External-action connectors remain simulation-only until their exact
  validation and approval gates pass.

## Baseline integrity

No file listed in `docs/approval/DESIGN-HASHES.sha256` is changed by this
amendment. The overlay and its completion evidence are additive and can be
removed without altering the approved architecture baseline.
