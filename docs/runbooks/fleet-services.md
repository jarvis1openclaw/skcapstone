# Fleet Services rollout (Phase 3)

## Pilot rollout, in order

1. Verify unit names on the target node BEFORE applying a spec:
   `systemctl --user list-units 'sk*'`. If a unit name differs from the
   pilot doc (for example the skchat daemon unit), fix the DOC, not the
   box.
2. Apply the pilot set on the control-plane node (.158):
   `for f in docs/fleet/pilot-services/*.json; do skfleet apply -f "$f"; done`
3. Run one controller pass and inspect:
   `skfleet reconcile && skfleet services && skfleet placements --kind service`
4. Watch one full sknoded cycle in report-only (default): statuses appear,
   ZERO actuation events. This is the safety soak.
5. Opt in actuation on .158 only: `skfleet actuation node-158 --enable`.
6. Acceptance drill (Card 3.1): `systemctl --user stop skwhisper@lumina`
   and confirm it is healed within 60s; set `"paused": true` in the doc,
   re-apply, stop again, confirm NO heal; unset paused. Kill-loop drill:
   break the unit (bad ExecStart), watch backoff events (10s, 20s, 40s),
   the CrashLooping condition, and exactly one sk-alert; repair the unit.
7. Freeze drill: `skfleet freeze --reason drill`, stop a pilot unit,
   confirm no heal and services stay up; `skfleet unfreeze`, confirm heal.
8. Wire the controller tick: add an skscheduler config job on .158 running
   `skfleet reconcile` every 60s (same jobs.yaml mechanism as existing
   jobs, notify: on_failure).
9. Enable actuation on node-41 after one clean day on .158. The local box
   stays report-only until explicitly decided otherwise (R4).

## Reversal

- One service: `"paused": true` + `skfleet apply -f <doc>`.
- One node: `skfleet actuation <node> --disable` (back to report-only).
- Fleet-wide: `skfleet freeze --reason <why>` (kill-switch; services keep
  running, all actuation halts everywhere).
