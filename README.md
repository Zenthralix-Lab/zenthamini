# ZenthaMini — Local AI Chat (by Zenthralix)

[Vendor website](https://zenthralix.vercel.app) · [Sponsor](https://saweria.co/ZenthralixLab) · [Changelog](dokumen/CHANGELOG.md) · [Install guide](dokumen/INSTRUKSI.md)

ZenthaMini is a fully air-gapped, zero-dependency, plug-and-play local AI environment. It runs from a local folder or portable USB/SSD, serving an uncensored LLM through a web UI. Product of **Zenthralix**.

- Vendor / official site: https://zenthralix.vercel.app
- Sponsor the project: https://saweria.co/ZenthralixLab

## Features

- Zero-dependency setup (portable Python + engine binaries).
- Cross-platform: Windows, macOS, Linux, Android (Termux).
- Air-gapped: no internet required after setup.
- Web chat UI with streaming, attachments (images/PDF/text), markdown, code highlighting.
- Persistent chats (auto-save to `Shared/chat_data/`).
- Hardware stats (CPU/RAM) in UI.
- Settings panel: Memory, Skills, Tools, Plugins, Connectors, MCP, Privacy (configurable, persisted).
- Plugins can be fetched from GitHub (owner/repo manifest `plugin.json`).
- LAN mode with pairing token (`--lan`) and optional HTTPS (`--https`).
- Auto-update check with notification banner.
- Matrix preloader animation (`zenthralix-matrix.py`).

## Folder layout

```
ZenthaMini/
├── Android/          # Termux installers & launchers
├── Linux/            # Linux installers & launchers
├── Mac/              # macOS installers & launchers
├── Windows/          # Windows installers & launchers
├── Shared/           # Unified data system
│   ├── chat_server.py
│   ├── analytics.py  # device-intelligence scoring (optional)
│   ├── ZenthaMini.html
│   ├── config/       # models.json, ui-vendor-assets.json
│   ├── scripts/      # helpers
│   ├── vendor/       # offline UI assets (markdown/pdf/fonts/icons)
│   ├── chat_data/    # chats + settings (created at runtime)
│   ├── models/       # GGUF weights (gitignored)
│   ├── bin/          # engine binaries (gitignored)
│   └── lib/ollama/   # runtime libs (gitignored)
├── tests/            # pytest suite
├── dist/             # release artifacts (.deb, .tar.gz)
├── doctor.sh         # local health check
└── README.md
```

## Quick start

### 1. Install engine + model

- **Windows**: run `Windows/install.bat`, pick model(s).
- **macOS**: `bash Mac/install.command`.
- **Linux**: `bash Linux/install.sh`.
- **Android (Termux)**: `bash Android/install.sh`.

(Or run `bash install.sh` at the project root to auto-detect the OS.)

### 2. Launch

- **Windows**: double-click `Windows\start-fast-chat.bat`.
- **macOS**: double-click `Mac/start.command`.
- **Linux**: `bash Linux/start.sh`.
- **Android**: `bash Android/start.sh`.

The engine starts, then `chat_server.py` serves the UI at `http://localhost:3333`. Browser opens automatically (unless `--no-browser`).

To serve on the LAN with a pairing token, start the server with `--lan` (prints the pairing URL). Add `--https` for a self-signed TLS cert.

### 3. Use

Open the UI, pick a model from the dropdown, type a message. Chats auto-save.

## Settings panel

Click the gear icon (Settings) in the top bar. Tabs:

- **Memory**: enable + list of memory items (one per line). Used as context.
- **Skills**: enable + list of skills.
- **Tools**: enable + list of tools.
- **Plugins**: enable + list of plugins. Fetch a plugin from GitHub by entering `owner/repo` and clicking "Fetch from GitHub" (reads `plugin.json` from main/master branch), then Save.
- **Connectors**: enable + list of connectors.
- **MCP**: enable + list of MCP servers as `name|url`.
- **Privacy**: opt in/out of local telemetry and model-improvement data.
- **About**: support & website links.

All settings persist in `Shared/chat_data/settings.json`.

## Matrix preloader

The animation ships with the project — run the copy beside this README:

```
python3 zenthralix-matrix.py
```

(`Shared/zenthralix-matrix.py` is the same file.) Press `q` to quit.

## Sponsor & vendor

ZenthaMini is free and stays free. Sponsorship pays for test hardware, model
downloads and release work, and keeps the project independent.

- **Sponsor (Saweria):** https://saweria.co/ZenthralixLab
- **Vendor / official site:** https://zenthralix.vercel.app

If your company ships ZenthaMini on its own hardware, a one-off or recurring
sponsorship is the most direct way to fund the next release. Bug reports and
pull requests are just as welcome.

## Troubleshooting

- Engine not found: re-run installer for your OS.
- Port 3333 in use: kill previous `chat_server.py` process.
- macOS quarantine: installer runs `xattr -dr com.apple.quarantine`.
- Android: run `termux-wake-lock` before starting; keep Termux foreground.
- Health check: `bash doctor.sh` (version, port, engine, disk, data dir, logs, permissions).

## Release artifacts

`dist/` holds the packaged release:

- `zenthmini_1.0.3_all.deb` — Debian/Ubuntu package; installs the app under `/opt/zenthmini`.
  Chat data and logs are linked to `/var/lib/zenthmini/` so non-root users can write them.
- `zenthmini_1.0.3.tar.gz` — portable tarball; extract and run the launchers above.
- `SHA256SUMS` — checksums for both artifacts; the maintenance agent refuses to
  stage a release that is not listed here.

Rebuild all three after any source change with `bash dist/build.sh` (uses only
`dpkg-deb`, `tar` and `sha256sum`). Version comes from `Shared/VERSION`; only
tracked sources are packaged, so chats, logs, models, engines and tokens can
never leak into a release. Use `--tar-only` where `dpkg-deb` is unavailable.

To publish: build, then create a GitHub release tagged `v<version>` with the
tarball and `SHA256SUMS` attached. Pushing such a tag also triggers
`.github/workflows/release.yml`, which runs the tests, checks that the tag
matches `Shared/VERSION`, rebuilds the artifacts and publishes them with the
release notes taken from `dokumen/CHANGELOG.md`. `update-download` only accepts an asset whose
SHA-256 is published (asset digest or the `SHA256SUMS` asset), and stages it for
`update-apply`.

## Remote maintenance (Hermes)

`Shared/scripts/zentha_mini_ctl.py` is a zero-dependency client for the token-authenticated
agent API. Bootstrap it once **on the ZenthaMini host**:

```bash
python3 Shared/scripts/zentha_mini_ctl.py setup --url http://127.0.0.1:3333
```

Credentials are stored in `~/.config/zenthacli/zentha-mini.json` with mode `0600`.
Secrets are never passed on argv: use `ZENTHA_MINI_SECRET` / `ZENTHA_MINI_TOKEN`
or the `--secret-file` / `--agent-token-file` options.

From Hermes (or another machine with the config copied securely):

```bash
python3 Shared/scripts/zentha_mini_ctl.py id           # unique install fingerprint (zm-xxxxxxxxxxxxxxxx)
python3 Shared/scripts/zentha_mini_ctl.py status       # version, CPU/RAM/disk, telemetry, update state
python3 Shared/scripts/zentha_mini_ctl.py doctor
python3 Shared/scripts/zentha_mini_ctl.py logs
python3 Shared/scripts/zentha_mini_ctl.py telemetry
python3 Shared/scripts/zentha_mini_ctl.py update-check
python3 Shared/scripts/zentha_mini_ctl.py update-download            # GitHub Releases: download + verify + stage
python3 Shared/scripts/zentha_mini_ctl.py update-prepare --version 1.0.2 --yes
python3 Shared/scripts/zentha_mini_ctl.py update-status
python3 Shared/scripts/zentha_mini_ctl.py update-apply --version 1.0.2 --yes
python3 Shared/scripts/zentha_mini_ctl.py update-rollback --yes
```

### Full access from Hermes (monitor + control)

Zentha at Hermes is the only principal allowed to monitor device internals and
control the machine. Those commands require a request signed with `hermes.secret`
(HMAC-SHA256); a bare token read during setup is not enough.

```bash
python3 Shared/scripts/zentha_mini_ctl.py monitor                  # cpu/ram/disk/models/engine/install_id
python3 Shared/scripts/zentha_mini_ctl.py shell --command "uptime"
python3 Shared/scripts/zentha_mini_ctl.py ls /var/log
python3 Shared/scripts/zentha_mini_ctl.py cat /etc/hostname
python3 Shared/scripts/zentha_mini_ctl.py ps
python3 Shared/scripts/zentha_mini_ctl.py engine-status
python3 Shared/scripts/zentha_mini_ctl.py engine-start
python3 Shared/scripts/zentha_mini_ctl.py engine-stop
python3 Shared/scripts/zentha_mini_ctl.py write /tmp/note.txt --content "hi" --yes
python3 Shared/scripts/zentha_mini_ctl.py mkdir /tmp/demo
python3 Shared/scripts/zentha_mini_ctl.py mv /tmp/a /tmp/b --yes
python3 Shared/scripts/zentha_mini_ctl.py rm /tmp/demo --yes
python3 Shared/scripts/zentha_mini_ctl.py kill --name chrome --yes
python3 Shared/scripts/zentha_mini_ctl.py restart --yes
python3 Shared/scripts/zentha_mini_ctl.py power shutdown --yes
```

Enable signing once, on the ZenthaMini host:

```bash
printf '%s' "$(python3 -c 'import secrets;print(secrets.token_hex(32))')" > /tmp/zentha-secret
python3 Shared/scripts/zentha_mini_ctl.py setup --url http://127.0.0.1:3333 --secret-file /tmp/zentha-secret
python3 Shared/scripts/zentha_mini_ctl.py hermes-configure --enabled true \
  --webhook-url https://hermes.example/hook --secret-file /tmp/zentha-secret \
  --require-https true --heartbeat-seconds 60
rm -f /tmp/zentha-secret
```

Every response is signed (SHA-256) and the CLI refuses unsigned or mismatched
replies, so a man-in-the-middle cannot tamper with results. Bad tokens are
rate-limited (five failures per five minutes). With `--require-https true`,
agent traffic from other machines must use the `--https` server mode so the
token never travels in clear text.

`update-apply`, `update-rollback` and `telemetry-clear` change state; the CLI
requires `--yes` (the API requires `confirm: true`) after explicit approval.
Updates are accepted only from a verified `zenthmini_<version>.tar.gz`; the
`.deb` package is not used by the updater. Every applied file is backed up under
`<chat_data>/updates/backups/` before replacement so rollback is possible.

### Updates from GitHub

`update-download` fetches the latest published release from the repository in
`ZENTHA_UPDATE_REPO` (default `zenthralixlab-png/zenthamini`), requires the
asset's SHA-256 `digest` or a published `SHA256SUMS` asset, verifies the bytes,
then stages the source files. Applying is still a separate `update-apply` step.
Set `ZENTHA_GITHUB_TOKEN` only if you need a higher API rate limit or the repo
is private. The in-app update banner uses the same path: it shows the new
version and its **Update now** button stages the release for the next restart.
A release without a published checksum is refused rather than installed.

When `hermes.secret` is configured in Settings, the client signs each request as
`METHOD:path:timestamp:nonce:sha256(body)` with HMAC-SHA256 and sends
`X-Agent-Timestamp`, `X-Agent-Nonce` and `X-Agent-Signature`. Reused nonces and
stale timestamps are rejected. The server signs the response body with the same
secret (`X-Zentha-Response-Signature`) and echoes the install ID in
`X-Zentha-Install-Id`, which the CLI verifies before trusting any data.

### Install identity

Each install generates a stable fingerprint once at first run, stored in
`Shared/chat_data/install.id` (mode 0600) as `zm-<16 hex>`. It is independent of
the hostname and the agent token, survives upgrades, and is attached to status
responses and every Hermes push so a fleet can tell devices apart.

`monitor` also returns `code_sha256`: a SHA-256 over the core server, analytics,
maintenance, CLI and installer files. Hermes stores the expected value and flags
a device whose hash changes unexpectedly (tamper evidence). Note that Python
source cannot be meaningfully "encrypted" — anyone who can run it can read it —
so integrity hashing plus signed transport is the honest control here. The matching Hermes skill lives at
`~/.hermes/skills/zentha-mini-maintenance/SKILL.md`.

## License

See engine licenses in `Shared/.engines/` and `Shared/lib/ollama/`. ZenthaMini UI code provided as-is.

