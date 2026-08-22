# SKCapstone registration runbook

Run preview first on each host. Keep the output as the drift record.

```bash
skcapstone register --dry-run
```

For chiwk12 WSL2:

```bash
/home/mrarch/.skenv/bin/skcapstone register --dry-run
```

Compare the proposed skill and MCP paths with the host's own home directory,
active profile, and detected environments. Do not copy generated configuration
between hosts. If the preview is approved, run registration for that host,
then immediately repeat the dry run and retain both outputs. Any unexpected
environment, path, or unrelated-file change is drift and blocks completion.
