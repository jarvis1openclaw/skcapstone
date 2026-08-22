# Court filing connector

`sklegal_filing` builds court-filing actions on the shared connector state
machine. It validates a forum, court, case number, and deterministic package
digest, then requires exact-version approval and a scoped capability before
simulation. It has no live provider or network dispatch path.
