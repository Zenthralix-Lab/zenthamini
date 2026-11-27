# ZenthaMini

### Private, portable AI chat that runs locally—even offline.

ZenthaMini is a local AI chat environment by **Zenthralix**. It runs from a local folder or portable USB/SSD and provides an LLM through a browser-based interface. Once installed, it can operate without an internet connection.

- [Official website](https://zenthralix.vercel.app)
- [Sponsor the project](https://saweria.co/ZenthralixLab)
- [Buy Me a Coffee](https://buymeacoffee.com/zenthralixlab)
- [Changelog](dokumen/CHANGELOG.md)
- [Installation guide](dokumen/INSTRUKSI.md)

## Highlights

- **Portable setup:** Bundles a portable Python runtime and engine binaries.
- **Cross-platform:** Supports Windows, macOS, Linux, and Android via Termux.
- **Offline operation:** No internet connection is required after setup.
- **Browser-based chat:** Supports streaming responses, image/PDF/text attachments, Markdown, and code highlighting.
- **Persistent conversations:** Chats are saved automatically in `Shared/chat_data/`.
- **Hardware information:** Displays CPU and memory usage in the interface.
- **Configurable settings:** Manage memory, skills, tools, plugins, connectors, MCP servers, and privacy preferences.
- **GitHub plugins:** Fetch plugins using an `owner/repository` identifier and a `plugin.json` manifest.
- **LAN access:** Share access using a pairing token with optional HTTPS.
- **Update notifications:** Checks for available updates and displays a notification in the app.

## Project structure

```text
ZenthaMini/
├── Android/          # Termux installers and launchers
├── Linux/            # Linux installers and launchers
├── Mac/              # macOS installers and launchers
├── Windows/          # Windows installers and launchers
├── Shared/           # Shared application files and data
│   ├── chat_server.py
│   ├── ZenthaMini.html
│   ├── config/       # Model and UI asset configuration
│   ├── scripts/      # Helper scripts
│   ├── vendor/       # Offline UI assets
│   ├── chat_data/    # Runtime chats and settings
│   ├── models/       # GGUF model files (gitignored)
│   ├── bin/          # Engine binaries (gitignored)
│   └── lib/ollama/   # Runtime libraries (gitignored)
├── tests/            # Pytest test suite
├── dist/             # Release packages
├── doctor.sh         # Local health check
└── README.md
```

## Installation and quick start

### 1. Install an engine and model

Run the installer for your platform:

| Platform | Installer |
| --- | --- |
| Windows | `Windows/install.bat` |
| macOS | `bash Mac/install.command` |
| Linux | `bash Linux/install.sh` |
| Android (Termux) | `bash Android/install.sh` |

You can also run `bash install.sh` from the project root to detect the operating system automatically.

### 2. Start ZenthaMini

| Platform | Launcher |
| --- | --- |
| Windows | `Windows\start-fast-chat.bat` |
| macOS | `Mac/start.command` |
| Linux | `bash Linux/start.sh` |
| Android (Termux) | `bash Android/start.sh` |

The engine starts first, followed by the chat server. Open [http://localhost:3333](http://localhost:3333) in your browser. The browser opens automatically unless you use `--no-browser`.

To make the server available on your local network, start it with `--lan`. The server will print a pairing URL. Add `--https` to enable a self-signed TLS certificate.

### 3. Start chatting

Choose a model from the dropdown and send a message. Conversations are saved automatically.

## Settings

Open **Settings** using the gear icon in the top bar. Available sections include:

- **Memory:** Enable memory and manage items provided as context.
- **Skills:** Enable and manage skills.
- **Tools:** Enable and manage tools.
- **Plugins:** Manage plugins or fetch one from GitHub using `owner/repository`. ZenthaMini reads `plugin.json` from the repository's `main` or `master` branch.
- **Connectors:** Enable and manage connectors.
- **MCP:** Configure MCP servers in `name|url` format.
- **Privacy:** Manage privacy preferences.
- **About:** Find project support and website links.

Settings are saved in `Shared/chat_data/settings.json`.

## Troubleshooting

- **Engine not found:** Run the installer for your operating system again.
- **Port 3333 is already in use:** Stop the existing `chat_server.py` process.
- **macOS quarantine warning:** The installer removes the quarantine attribute using `xattr -dr com.apple.quarantine`.
- **Android performance or sleep issues:** Run `termux-wake-lock` before starting and keep Termux in the foreground.
- **Run a health check:** Use `bash doctor.sh` to check the version, port, engine, disk space, data directory, logs, and permissions.

## Release packages

Release artifacts are stored in `dist/`:

- `zenthmini_1.0.3_all.deb` — Debian/Ubuntu package. Installs the app under `/opt/zenthmini`; chat data and logs are linked to `/var/lib/zenthmini/`.
- `zenthmini_1.0.3.tar.gz` — Portable archive.
- `SHA256SUMS` — Checksums for the release artifacts.

Rebuild the packages and checksums after source changes:

```bash
bash dist/build.sh
```

The build uses `dpkg-deb`, `tar`, and `sha256sum`. Use `--tar-only` on systems without `dpkg-deb`. The version is read from `Shared/VERSION`.

Only tracked source files are packaged. Chats, logs, models, engines, and tokens are excluded from release artifacts.

### Publishing a release

Create a GitHub release tagged `v<version>` and attach the tarball and `SHA256SUMS`. Pushing a release tag also triggers `.github/workflows/release.yml`, which runs tests, checks the tag against `Shared/VERSION`, rebuilds the artifacts, and publishes them with notes from `dokumen/CHANGELOG.md`.

The updater accepts only verified `zenthmini_<version>.tar.gz` archives; it does not use the `.deb` package. It verifies the SHA-256 checksum before staging an update. Before replacing files, it creates backups under `<chat_data>/updates/backups/` so the update can be rolled back.

## Sponsorship

ZenthaMini is free to use, and the project is intended to remain free. Sponsorship helps fund test hardware, model downloads, and release work while supporting the project's independence.

- [Sponsor via Saweria](https://saweria.co/ZenthralixLab)
- [Buy Me a Coffee](https://buymeacoffee.com/zenthralixlab)
- [Visit the official website](https://zenthralix.vercel.app)

Bug reports and pull requests are also welcome.

## License

ZenthaMini is released under the [MIT License](LICENSE).
```
