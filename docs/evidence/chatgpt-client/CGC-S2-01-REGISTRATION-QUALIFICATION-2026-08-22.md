# CGC-S2-01 SKCapstone registration qualification

Date: 2026-08-22
Card: 8b9ee8b3
Operator: jarvis

## Dry-run determinism

The registration command was run twice on the local ChatGPT/Codex host:

```bash
skcapstone register --dry-run > register-1.txt
skcapstone register --dry-run > register-2.txt
cmp -s register-1.txt register-2.txt
```

The two outputs were byte-identical and reported `No changes made (dry run)`.
The detected environments were Claude Code, VS Code, OpenCode, and Codex. The
output proposed SK skill and MCP entries without mutating any configuration.

The same dry run was executed on chiwk12 WSL2 as user `mrarch` using its own
binary at `/home/mrarch/.skenv/bin/skcapstone`. It detected Claude Code,
OpenCode, and Codex and also reported no changes. The differing environment
set is host-specific and was preserved rather than harmonized.

## Host path and profile evidence

| Host | Home | Binary | Result |
| --- | --- | --- | --- |
| local chiap04 session | `/home/skuser01` | `/home/skuser01/.skenv/bin/skcapstone` | deterministic dry run |
| chiwk12 WSL2 | `/home/mrarch` | `/home/mrarch/.skenv/bin/skcapstone` | successful dry run |

Registration output uses the active user's detected home and environment
paths. No path from the local host was copied into the WSL2 result.

## Idempotence and preservation decision

Only dry-run mode was used during this qualification. Because dry-run output
was stable on both hosts and no file mutation was authorized by this card's
qualification pass, unrelated user configuration was not rewritten. A future
write-mode run must snapshot the target files and compare a second dry run
before and after registration.

## Acceptance evidence

- Dry-run drift reporting is repeatable and mutation-free.
- Both target hosts were exercised with their own home paths and binaries.
- Host-specific environment detection was preserved.
- No unrelated configuration or credentials were modified.
