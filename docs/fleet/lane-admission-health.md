# Fleet lane admission health

`scripts/fleet/skfleet-rotate.py` reads one snapshot before selecting work. The
default path is `~/.skcapstone/evidence/fleet-lane-health.json`; an operator may
set `SKFLEET_LANE_HEALTH_PATH` to the authoritative publisher's atomic output.
The read is capped at 65,536 bytes and the snapshot expires after 120 seconds.
Missing, malformed, oversized, future-dated, and stale snapshots admit no lane.

The publisher contract is:

```json
{
  "schema_version": 1,
  "observed_at": 1788465600.0,
  "runtime_revision": "exact-runtime-revision",
  "lanes": [
    {
      "lane": "codex",
      "model": "sk-codex",
      "observed_state": "healthy",
      "quarantine_state": "clear",
      "owner_available": true
    }
  ]
}
```

Admission requires an exact lane and model match, `healthy` observed state,
`clear` quarantine state, an available owner, a nonempty runtime revision, and
a fresh timestamp. A failed lane does not consume capacity or create a claim.
An ordinary compatible card may move to another healthy lane. A lane-exclusive
card remains unowned until its exact lane recovers. Repeated blocker records are
limited to one per card per UTC hour.
