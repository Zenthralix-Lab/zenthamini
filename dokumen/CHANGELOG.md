# Changelog

## Unreleased

### Remote agent: Hermes monitoring + full control
- Fixed the remote-agent gate: `/api/agent/*` no longer requires a browser login
  session or a private `Host` header, so Hermes on another machine (or behind a
  reverse proxy/tunnel) can actually reach the agent API. The agent token and
  optional HMAC are the only credentials required.
- Added unique per-install fingerprint `zm-<16 hex>` in `chat_data/install.id`
  (0600, stable across restarts/upgrades). It is returned by `status`,
  `install_id`, every signed agent response header and every Hermes push.
- Fixed the `shell` wire format in both directions: the CLI used to send the
  command to run under the same `command` key as the agent command, so every
  `shell` call arrived as "unknown command"; on the way back, the shell result
  overwrote the response's `command` field. The shell command now travels in
  `cmd` in requests and responses, while `command` stays `"shell"`.
- Added full-access commands for the Hermes agent: `shell`, `fs_list`, `fs_read`,
  `fs_write`, `fs_mkdir`, `fs_delete`, `fs_move`, `process_list`, `process_kill`,
  `engine_start`, `engine_stop`, `engine_status`, `server_restart`, `power` and
  `monitor`. Destructive commands require `confirm: true`.
- Privilege boundary: full-access and device-internal monitoring commands require
  HMAC-SHA256 signing with `hermes.secret`. A bare agent token cannot run them.
- Added outbound heartbeat/registration: on start and every
  `hermes.heartbeat_seconds` the server pushes a signed `startup`/`heartbeat`
  envelope (signed with the install ID) to the Hermes webhook.
- Optional `hermes.require_https` rejects non-TLS agent traffic from other
  machines (anti-MITM); plain HTTP webhooks are now allowed only for
  loopback/private hosts.

### Security hardening
- Agent responses are signed with HMAC-SHA256 (`X-Zentha-Response-Signature`);
  the CLI refuses unsigned or mismatched replies (downgrade/MITM protection).
- Agent-token brute force is throttled (five failures per IP per five minutes).
- Removed string interpolation from the browser-history SQLite queries (SQL
  injection hardening); only literal statements run.
- Agent body limit raised to 2 MB for file writes, still capped and rate-limited.

### Tests & CI
- Added an end-to-end test that runs the real `zentha_mini_ctl.py` process against
  the agent API (setup, status, shell, file commands and the unsigned-request
  rejection), covering the wire format the unit tests stub out.
- Added a GitHub Actions workflow running the suite on Python 3.10 and 3.12.

## 1.0.2 (2026-11-25)
- Added a zero-dependency remote maintenance CLI (`Shared/scripts/zentha_mini_ctl.py`)
  and new agent commands: `maintenance_status`, `update_check`, `update_status`,
  `update_prepare`, `update_apply`, `update_rollback`, `telemetry_clear`.
- Verified updates: only local `dist/*.tar.gz` artifacts listed in `dist/SHA256SUMS`
  can be staged; apply creates backups and rollback restores them. `.deb` files
  are deliberately refused. Apply/rollback/telemetry-clear require `confirm: true`.
- Agent authentication hardened: token headers only (no `?token=`), HMAC now covers
  method, path, timestamp, nonce and request body, with replay protection. Settings
  reads redact the Hermes secret and agent token. Oversized bodies and rapid login
  attempts are rejected.
- Frontend: markdown HTML sanitized, inline `onclick` replaced with delegated
  handlers, streamed NDJSON lines buffered across chunks, telemetry snapshots
  deleted when consent is turned off, update links validated.
- `dist/build.sh` now emits `SHA256SUMS` and excludes runtime update markers.
  `doctor.sh` resolves the project root correctly from `Shared/` and uses portable
  `stat`. Windows downloads no longer disable TLS revocation checks; the Linux
  installer reports a missing `zstd` dependency clearly and validates custom
  model names/URLs. The Android launcher now starts the chat server before
  opening the browser. The `.deb` package links chat data/logs into
  `/var/lib/zenthmini/` so non-root users can write them.

## 1.0.0 (2026-11-19)
- Initial public release.
- Local AI chat UI with streaming, attachments, markdown.
- Cross-platform launchers (Windows, macOS, Linux, Android).
- Settings panel: Memory, Skills, Tools, Plugins, Connectors, MCP.
- Plugin fetch from GitHub.
- LAN mode with pairing token (`--lan`).
- Auto-update check with notification banner.
- SOUL.md (AI core "Zentha").
- Security hardening of static file serving.

## 1.0.1 (2026-11-21)
- Host/CORS private-network check now parses IPs: `172.0-15.x` and `172.32+` are
  public and no longer accepted (the old `startswith("172.")` matched them).
- `dist/` rebuilt from current source: the shipped `chat_server.py`,
  `ZenthaMini.html` and `analytics.py` now match the tree (previously stale).
- `dist/build.sh` added: rebuilds the `.deb` and `.tar.gz` from source.
- README corrected: real launcher/installer filenames, in-project matrix script
  path, Privacy tab, LAN/HTTPS flags, release artifacts and `doctor.sh`.
- Tests: added a regression for the public-`172` host check (23 passing).
