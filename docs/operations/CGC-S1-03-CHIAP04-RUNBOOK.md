# chiap04 ChatGPT update and rollback runbook

Use an approved maintenance window and an operator with package-management
privileges. Capture the current version and package candidate before changing
anything.

```bash
dpkg-query -W -f='${Version}\\n' chatgpt
apt-cache policy chatgpt
apt-get --simulate install chatgpt=<candidate>
apt-get --simulate remove chatgpt
```

If both simulations are expected, perform the update, verify the client and
the four stdio servers, and retain the exact prior version for rollback:

```bash
sudo apt-get update
sudo apt-get install chatgpt=<candidate>
systemctl --user --no-pager status chatgpt 2>/dev/null || true
ps -eo pid,comm,rss,args | grep -E 'skchat-mcp|skmemory-mcp|skcomms-mcp|skcapstone-mcp'
sudo apt-get install chatgpt=<prior-version>
```

Do not purge a running production client as part of routine qualification.
The purge path is simulation-only until a human-approved rollback window is
opened. If the repository no longer serves the prior version, stop rather
than substituting an unverified package.
