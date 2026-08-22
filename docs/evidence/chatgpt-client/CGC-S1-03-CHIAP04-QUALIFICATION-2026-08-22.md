# CGC-S1-03 chiap04 qualification evidence

Date: 2026-08-22
Host: chiap04
Card: 37871679
Mode: read-only qualification and package-manager simulation

## Results

The installed ChatGPT package is `26.818.22352` and the repository candidate
is `26.818.31338`. The reproducible upgrade simulation reports one upgrade and
no removals. The reproducible purge simulation reports removal of `chatgpt`.
Neither simulation changed the host.

```text
apt-get --simulate install chatgpt=26.818.31338
Inst chatgpt [26.818.22352] (26.818.31338 stable [amd64])
Conf chatgpt (26.818.31338 stable [amd64])

apt-get --simulate remove chatgpt
Remv chatgpt [26.818.22352]
```

The real update and rollback path is therefore:

```bash
sudo apt-get update
sudo apt-get install chatgpt=26.818.31338
sudo apt-get install chatgpt=26.818.22352
```

The installed version must be captured before the update with
`dpkg-query -W -f='${Version}\\n' chatgpt`. If the previous version is no
longer available from the configured repository, stop and restore the pinned
package artifact through the approved package mirror. Do not use an unverified
download.

## MCP and resource baseline

The four expected stdio servers were observed on chiap04:

| Server | Observed RSS (KiB) |
| --- | ---: |
| skchat-mcp | 16,064 |
| skmemory-mcp | 14,164 |
| skcomms-mcp | 14,784 |
| skcapstone-mcp | 14,360 |

The host also had the Codex security stdio server, which is a separate
security plugin process and is not counted in the four-server baseline.
Repeated server instances were present because multiple governed sessions
were running. Baseline collection is intentionally observational and does not
terminate or restart a session.

Reproduce the process and memory baseline with:

```bash
ps -eo pid,comm,rss,args | grep -E 'skchat-mcp|skmemory-mcp|skcomms-mcp|skcapstone-mcp'
free -m
```

## Least privilege and preview limitations

The AppArmor kernel module is loaded, but the unprivileged qualification user
cannot read the complete profile set. This is a documented visibility limit,
not evidence that profiles are absent. A privileged operator must capture
`aa-status` during release qualification.

`systemd-analyze security` reports a mixed service posture on the desktop
host, including exposed or unsafe system services unrelated to the ChatGPT
client. The preview qualification therefore does not claim a hardened whole
host. Service-specific hardening and a privileged AppArmor review remain gates
for production deployment.

## Acceptance evidence

- Update and rollback commands are pinned to observed package versions and are
  reproducible with simulation before any mutation.
- Purge and reinstall behavior is represented by the package-manager commands;
  no purge was executed during this qualification.
- The four stdio MCP child processes and memory baseline are recorded.
- AppArmor and sandbox limitations are explicitly documented.
- No live external action, package mutation, process restart, or credential
  change was performed.
