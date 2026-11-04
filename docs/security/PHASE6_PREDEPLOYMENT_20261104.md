# PHASE 6 VPS Pre-deployment Preparation — 2026-11-04

## Scope and safety boundary

This preparation did not deploy a Bot, alter CryptoFX, change its Docker/UFW/SSH
configuration, access a Coincheck private API, or call a paid OpenAI API. The
normal iMac Bot, its DB, balances, and volumes were not targeted.

## 1. Backup and key disaster recovery

### Drive observation (read-only)

At 2026-11-04, the following Google Drive folders were independently read back:

| Folder | Sharing | Direct files |
| --- | --- | --- |
| `Coincheck AI Bot — DR Recovery — 2026-11-04` | Owner only; not shared | 0 |
| `Coincheck AI Bot — Encrypted Archive Offsite — 2026-11-04` | Owner only; not shared | 0 |

The new archive name `paperbot_20261104T053119Z.dump.age` was not present.
The security-interrupted upload was **not retried**. No age identity, archive,
or checksum was uploaded in this phase.

### Required separation design

- Google Drive is reserved for `.dump.age` and its `.sha256` sidecar only; no
  age private identity belongs in the same Google account.
- The recovery identity must be copied by an owner-approved process to a
  different security and failure domain (for example, an owner-controlled
  encrypted offline medium or separate security vault). It must not be on the
  iMac, its attached HDD, GitHub, Docker, the VPS, or this Google account.
- The existing two legacy archives are retained, checksum-valid, and listed as
  **restore-unverified**. They were neither deleted nor overwritten.

### Owner approvals still required

1. Select and approve the independent recovery-key vault/medium and its access
   policy, retention policy, and authorized recovery operators.
2. Approve one controlled identity-copy operation to that vault, followed by a
   recovery-access test that never prints key material.
3. Approve the deferred Google Drive upload of the encrypted archive and
   checksum only, then a download/SHA-256 comparison and a restore into a new
   `paperbot_restore_*` DB using the independently stored identity.

No alternative tool or path may be used to bypass the earlier upload safety
interruption. Until these approvals and tests are complete, independent DR is
not established.

## 2. GitHub release protection

| Item | Evidence |
| --- | --- |
| `main` HEAD | `9ab6bf79fb07342ae6026e332d0a2b72081d6cce` |
| Security candidate HEAD | `97cece518662b55ae1ad6452c593fe4e11864bdc` |
| Review PR | [#1](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/pull/1), `security/do-security-hardening-20261103` → `main` |
| PR state | Open; `REVIEW_REQUIRED`; required `security` CI is successful |
| Required status check | `security`, strict/up-to-date branch required |
| Review policy | 1 approval, stale-review dismissal, last-push approval, conversation resolution |
| Direct-push controls | Protection enforced for administrators; force push and branch deletion disabled |

The PR is intentionally not merged. The formal release candidate is
`97cece5`; its runtime sources, Dockerfile, Compose, migrations, and locked
dependencies are unchanged from `eb81551`. The verified corresponding runtime
image is `sha256:f7428dab08200902ec1582fb6795732792f6930a1320d12208c48a8b4b8a3421`,
with OCI revision `eb8155145965024fe625ebe048b9ede5c10bd091`.

## 3. CryptoFX pre-deployment read-only verification

- DigitalOcean Droplet: `CryptoFX`, ID `615557110`, public IPv4
  `157.245.193.247`, status `active`, Ubuntu 24.04 LTS, Singapore region.
- Attached Cloud Firewall `coincheck-paperbot-fw` permits inbound TCP/22 only
  from the configured management `/32`; it does not expose 8097, 8000, or 5432.
  Egress is limited to DNS, HTTP/HTTPS, and NTP.
- Strict-host-key SSH to the verified IPv4 succeeded as `bot-admin`. The host
  reported Ubuntu 24.04, synchronized UTC time, active NTP, TCP/22 on IPv4 and
  IPv6, and local DNS listeners only.
- The configured `CryptoFX` SSH alias currently points at an unresolved
  hostname, so the verified IP path was used without changing SSH config.
- UFW, Docker daemon/container state, and effective sshd policy remain
  unverified: the approved account requires sudo for UFW/sshd and lacks Docker
  socket access. No privilege escalation or configuration change was attempted.

The existing Compose design is VPS-ready but not deployed: the frontend binds
only `127.0.0.1:${FRONTEND_PORT}:8080`, while DB and API have no host ports;
the intended VPS value is `FRONTEND_PORT=8097`. Runtime secrets are file-mounted
Compose secrets, and `DRY_RUN=true`, `LIVE_TRADING_ENABLED=false`, and
`NEWS_MODE=mock` are fixed in the service environment. The release/rollback,
SSH-tunnel, monitoring, and incident procedures are maintained in
`CONFIG_RELEASE_ROLLBACK.md`, `DIGITALOCEAN_SSH_VPS_RUNBOOK.md`, and
`MONITORING_INCIDENT.md`.

## Remaining PHASE 6 acceptance gate

1. Complete the separately authorized key-vault and encrypted-archive DR drill.
2. Obtain normal sudo/Docker read-only access on CryptoFX, then verify UFW,
   effective sshd policy, Docker state, Compose configuration, and no exposed
   service ports.
3. Obtain an independent approval on PR #1. Do not merge until a separate,
   explicit merge authorization is provided.

**PHASE 6: BLOCKED.** CryptoFX DRY_RUN deployment is not authorized or ready
while independent DR storage and privileged VPS read-only acceptance remain
incomplete.

## Continuation evidence — 2026-11-04

The interrupted Drive upload was not retried. No independently approved
offline medium or vault was identified in this continuation, so no recovery
identity copy, offsite archive upload, download checksum, or further restore
drill was performed.

CryptoFX read-only capability was narrowed without changing it: `bot-admin` is
in the `sudo` group, but non-interactive sudo is unavailable; the Docker socket
is mode `660 root:docker`, and the account is not in the `docker` group. UFW
and sshd binaries are present. This confirms that one owner-authenticated sudo
session can perform the remaining read-only UFW/sshd/Docker checks without
adding NOPASSWD or changing group membership. No password was requested,
captured, or supplied to an automation.

PR #1 is open at `b8a74c7`, both required Security CI runs pass, and a
non-sensitive [review packet](https://github.com/OPP-studio-macchino/coincheck-ai-paperbot/pull/1#issuecomment-6624736218)
was posted. It has zero reviews, no requested reviewers, and the repository's
only collaborator visible to the connected account is the PR author/admin.
There is therefore no eligible independent reviewer to request from this
account. An owner must add or nominate a qualified, separate reviewer; the
author and automated agents must not approve the PR.

The remaining gates and **PHASE 6: BLOCKED** disposition are unchanged.
