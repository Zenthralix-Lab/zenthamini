#!/usr/bin/env python3
"""ZenthaMini remote maintenance client (used by the Hermes agent).

Zero-dependency CLI over ZenthaMini's token-authenticated agent API. The agent
token and optional HMAC secret are read from 0600 config files or environment
variables -- never from argv (argv is visible to other users via /proc).

Bootstrap from the same host:

    python3 zentha_mini_ctl.py setup --url http://127.0.0.1:3333

Then, from Hermes:

    python3 zentha_mini_ctl.py status
    python3 zentha_mini_ctl.py telemetry
    python3 zentha_mini_ctl.py update-check
    python3 zentha_mini_ctl.py update-download            # from GitHub Releases
    python3 zentha_mini_ctl.py update-prepare --version 1.0.2 --yes
    python3 zentha_mini_ctl.py update-apply --version 1.0.2 --yes   # needs approval
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config/zenthacli/zentha-mini.json"
DEFAULT_URL = "http://127.0.0.1:3333"
TIMEOUT = 30

READ_COMMANDS = {
    "status": "maintenance_status",
    "maintenance": "maintenance_status",
    "doctor": "run_doctor",
    "settings": "settings_get",
    "telemetry": "telemetry_get",
    "update-check": "update_check",
    "update-status": "update_status",
    "analytics": "analytics",
}
WRITE_COMMANDS = {
    "telemetry-clear": "telemetry_clear",
    "update-prepare": "update_prepare",
    "update-apply": "update_apply",
    "update-rollback": "update_rollback",
}


def _load_config(path: Path) -> dict:
    data = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read config {path}: {exc}")
    data["base_url"] = os.environ.get("ZENTHA_MINI_URL") or data.get("base_url") or DEFAULT_URL
    data["token"] = os.environ.get("ZENTHA_MINI_TOKEN") or data.get("token") or ""
    data["secret"] = os.environ.get("ZENTHA_MINI_SECRET") or data.get("secret") or ""
    return data


def _save_config(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _request(cfg: dict, method: str, path: str, payload: dict | None = None) -> dict:
    if not cfg.get("token"):
        raise SystemExit("no agent token: run `setup` first or set ZENTHA_MINI_TOKEN")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Agent-Token", cfg["token"])
    secret = cfg.get("secret") or ""
    if secret:
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_hash = hashlib.sha256(body or b"").hexdigest()
        req.add_header("X-Agent-Timestamp", ts)
        req.add_header("X-Agent-Nonce", nonce)
        msg = f"{method}:{path}:{ts}:{nonce}:{body_hash}".encode("utf-8")
        req.add_header("X-Agent-Signature", hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            headers = resp.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = raw.decode("utf-8", "replace")[:300]
        raise SystemExit(f"HTTP {exc.code} from ZenthaMini: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach ZenthaMini at {cfg['base_url']}: {exc.reason}")
    try:
        result = json.loads(raw or b"{}")
    except ValueError:
        raise SystemExit("ZenthaMini returned non-JSON data")
    # Integrity: when HMAC is configured, every response must carry a matching
    # SHA-256 signature. A missing signature is treated as a downgrade/MITM attempt.
    secret = cfg.get("secret") or ""
    if secret:
        signature = headers.get("X-Zentha-Response-Signature") or ""
        if not signature:
            raise SystemExit("unsigned response; refusing (possible MITM/downgrade)")
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise SystemExit("response signature mismatch; refusing tampered data")
    install_id = headers.get("X-Zentha-Install-Id") or ""
    if isinstance(result, dict) and install_id and "install_id" not in result:
        result["install_id"] = install_id
    return result


def _command(cfg: dict, name: str, fields: dict | None = None) -> dict:
    return _request(cfg, "POST", "/api/agent/command", {"command": name, **(fields or {})})


def _setup(args, cfg: dict) -> dict:
    """Fetch the local bootstrap token over loopback and store it with 0600."""
    url = args.url.rstrip("/")
    boot = urllib.request.Request(url + "/api/agent/token")
    try:
        with urllib.request.urlopen(boot, timeout=10) as resp:
            data = json.loads(resp.read() or b"{}")
    except Exception as exc:
        raise SystemExit(f"cannot bootstrap from {url} (must run on the ZenthaMini host): {exc}")
    token = data.get("token")
    if not token:
        raise SystemExit("agent token endpoint returned no token")
    secret = cfg.get("secret", "")
    if args.secret_file:
        try:
            secret = Path(args.secret_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"cannot read {args.secret_file}: {exc}")
    out = {
        "base_url": url,
        "token": token,
        "secret": secret,
    }
    if data.get("install_id"):
        out["install_id"] = data["install_id"]
    _save_config(Path(args.config), out)
    note = ("token stored with mode 0600; HMAC signing enabled"
            if secret else
            "token stored with mode 0600; run hermes-configure --secret-file <file> "
            "to enable HMAC signing and full-access commands")
    return {"ok": True, "config": args.config, "base_url": url,
            "install_id": data.get("install_id"), "note": note}


def _run(args, cfg: dict) -> dict:
    if args.action == "setup":
        return _setup(args, cfg)
    if args.action == "raw":
        fields = json.loads(args.payload) if args.payload else {}
        return _command(cfg, args.name, fields)
    if args.action == "hermes-configure":
        patch = {}
        if args.enabled is not None:
            patch["enabled"] = args.enabled == "true"
        if args.webhook_url is not None:
            patch["webhook_url"] = args.webhook_url
        if args.require_https is not None:
            patch["require_https"] = args.require_https == "true"
        if args.heartbeat_seconds is not None:
            patch["heartbeat_seconds"] = max(15, min(int(args.heartbeat_seconds), 3600))
        for key, path in (("secret", args.secret_file),
                          ("agent_token", args.agent_token_file)):
            if path is None:
                continue
            try:
                content = Path(path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise SystemExit(f"cannot read {path}: {exc}")
            if not content:
                raise SystemExit(f"{path} is empty")
            patch[key] = content
        if not patch:
            raise SystemExit("provide at least one Hermes field to configure")
        result = _command(cfg, "settings_set", {"settings": {"hermes": patch}})
        return {"ok": result.get("ok", False), "command": "hermes-configure",
                "fields": sorted(patch), "detail": result}
    if args.action == "status-live":
        return _request(cfg, "GET", "/api/agent/status")
    if args.action == "logs":
        return _request(cfg, "GET", "/api/agent/logs")

    # ── Identity + monitoring ──────────────────────────────
    if args.action == "id":
        return _command(cfg, "install_id")
    if args.action == "monitor":
        return _command(cfg, "monitor")

    # ── Full-access device control ─────────────────────────
    if args.action == "shell":
        # "cmd" holds the shell command; top-level "command" is the agent command.
        fields = {"cmd": args.command, "timeout": args.timeout}
        if args.cwd:
            fields["cwd"] = args.cwd
        return _command(cfg, "shell", fields)
    if args.action == "ls":
        return _command(cfg, "fs_list", {"path": args.path, "limit": args.limit})
    if args.action == "cat":
        return _command(cfg, "fs_read", {"path": args.path, "max_bytes": args.max_bytes})
    if args.action == "mkdir":
        return _command(cfg, "fs_mkdir", {"path": args.path})
    if args.action == "write":
        if not args.yes:
            raise SystemExit("'write' changes state; re-run with --yes after approval")
        content = args.content
        if args.from_file:
            try:
                content = Path(args.from_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise SystemExit(f"cannot read {args.from_file}: {exc}")
        return _command(cfg, "fs_write", {"path": args.path, "content": content,
                                          "append": bool(args.append)})
    if args.action == "rm":
        if not args.yes:
            raise SystemExit("'rm' deletes data; re-run with --yes after approval")
        return _command(cfg, "fs_delete", {"path": args.path, "confirm": True})
    if args.action == "mv":
        if not args.yes:
            raise SystemExit("'mv' changes state; re-run with --yes after approval")
        return _command(cfg, "fs_move", {"from": args.src, "to": args.dst})
    if args.action == "ps":
        return _command(cfg, "process_list", {"limit": args.limit})
    if args.action == "kill":
        if not args.yes:
            raise SystemExit("'kill' is destructive; re-run with --yes after approval")
        fields = {"confirm": True, "all": bool(args.all)}
        if args.pid is not None:
            fields["pid"] = args.pid
        if args.name:
            fields["name"] = args.name
        return _command(cfg, "process_kill", fields)
    if args.action in ("engine-status", "engine-start", "engine-stop"):
        return _command(cfg, args.action.replace("-", "_"))
    if args.action == "restart":
        if not args.yes:
            raise SystemExit("'restart' changes state; re-run with --yes after approval")
        return _command(cfg, "server_restart", {"confirm": True})
    if args.action == "power":
        if not args.yes:
            raise SystemExit("'power' is destructive; re-run with --yes after approval")
        return _command(cfg, "power", {"action": args.mode, "confirm": True})

    fields = {}
    if args.action == "chat":
        fields = {"prompt": args.prompt, "chat_id": args.chat_id, "model": args.model}
        fields = {k: v for k, v in fields.items() if v is not None}
        return _command(cfg, "chat", fields)
    if args.action == "analytics":
        if args.markdown:
            fields["format"] = "markdown"
        return _command(cfg, READ_COMMANDS[args.action], fields)
    if args.action == "update-download":
        for key, value in (("version", args.version), ("repo", args.repo), ("asset", args.asset)):
            if value:
                fields[key] = value
        return _command(cfg, "update_download", fields)
    if args.action in ("update-prepare", "update-apply"):
        if args.version:
            fields["version"] = args.version
        if args.artifact:
            fields["artifact"] = args.artifact
    if args.action in WRITE_COMMANDS:
        if not args.yes:
            raise SystemExit(f"'{args.action}' changes state; re-run with --yes after approval")
        fields["confirm"] = True
    return _command(cfg, READ_COMMANDS.get(args.action) or WRITE_COMMANDS[args.action], fields)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ZenthaMini remote maintenance client")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="credentials file (0600)")
    parser.add_argument("--json", action="store_true", help="compact JSON (default: indented)")
    sub = parser.add_subparsers(dest="action", required=True)

    setup = sub.add_parser("setup", help="fetch local agent token and write the 0600 config")
    setup.add_argument("--url", default=DEFAULT_URL)
    setup.add_argument("--secret-file", help="file containing the HMAC secret set in ZenthaMini Settings")

    hermes = sub.add_parser("hermes-configure", help="update Hermes settings (secret/token via --secret-file or env)")
    hermes.add_argument("--enabled", choices=("true", "false"))
    hermes.add_argument("--webhook-url")
    hermes.add_argument("--secret-file", help="file containing the HMAC secret (never pass secrets on argv)")
    hermes.add_argument("--agent-token-file", help="file containing the agent token")
    hermes.add_argument("--require-https", choices=("true", "false"),
                        help="reject non-TLS agent traffic from other machines")
    hermes.add_argument("--heartbeat-seconds", type=int,
                        help="how often ZenthaMini pushes heartbeat/metrics to Hermes (15-3600)")

    sub.add_parser("id", help="show this install's unique fingerprint")
    sub.add_parser("monitor", help="live metrics for Hermes (cpu/ram/disk/models/engine)")
    sub.add_parser("status-live", help="live status (GET /api/agent/status)")

    shell = sub.add_parser("shell", help="run a command on the ZenthaMini device")
    shell.add_argument("--command", required=True)
    shell.add_argument("--timeout", type=int, default=30)
    shell.add_argument("--cwd")

    ls = sub.add_parser("ls", help="list a directory on the device")
    ls.add_argument("path", nargs="?", default=".")
    ls.add_argument("--limit", type=int, default=500)

    cat = sub.add_parser("cat", help="read a file from the device")
    cat.add_argument("path")
    cat.add_argument("--max-bytes", type=int, default=200000)

    mkdir = sub.add_parser("mkdir", help="create a directory")
    mkdir.add_argument("path")

    write = sub.add_parser("write", help="write a file (requires --yes)")
    write.add_argument("path")
    write.add_argument("--content", default="")
    write.add_argument("--from-file", help="read content from a local file instead")
    write.add_argument("--append", action="store_true")
    write.add_argument("--yes", action="store_true")

    rm = sub.add_parser("rm", help="delete a file or directory (requires --yes)")
    rm.add_argument("path")
    rm.add_argument("--yes", action="store_true")

    mv = sub.add_parser("mv", help="move/rename a path (requires --yes)")
    mv.add_argument("src")
    mv.add_argument("dst")
    mv.add_argument("--yes", action="store_true")

    ps = sub.add_parser("ps", help="list processes on the device")
    ps.add_argument("--limit", type=int, default=200)

    kill = sub.add_parser("kill", help="terminate a process (requires --yes)")
    kill.add_argument("--pid", type=int)
    kill.add_argument("--name")
    kill.add_argument("--all", action="store_true")
    kill.add_argument("--yes", action="store_true")

    sub.add_parser("engine-status", help="is the AI engine running?")
    sub.add_parser("engine-start", help="start the AI engine")
    sub.add_parser("engine-stop", help="stop the AI engine")

    restart = sub.add_parser("restart", help="restart the chat server (requires --yes)")
    restart.add_argument("--yes", action="store_true")

    power = sub.add_parser("power", help="shutdown/reboot/sleep the device (requires --yes)")
    power.add_argument("mode", choices=("shutdown", "reboot", "sleep"))
    power.add_argument("--yes", action="store_true")
    sub.add_parser("status", help="aggregate maintenance status")
    sub.add_parser("doctor")
    sub.add_parser("settings")
    sub.add_parser("logs")
    sub.add_parser("telemetry")
    clear = sub.add_parser("telemetry-clear")
    clear.add_argument("--yes", action="store_true")
    sub.add_parser("update-check")
    sub.add_parser("update-status")
    pull = sub.add_parser("update-download",
                          help="download, verify and stage the latest release from GitHub")
    pull.add_argument("--version", help="release to install (default: latest)")
    pull.add_argument("--repo", help="GitHub owner/name (default: build's repo or ZENTHA_UPDATE_REPO)")
    pull.add_argument("--asset", help="exact release asset basename")
    prep = sub.add_parser("update-prepare", help="verify + stage a local release artifact")
    prep.add_argument("--version")
    prep.add_argument("--artifact", help="basename in the app's dist/ directory")
    prep.add_argument("--yes", action="store_true", help="confirm staging (writes to updates/staged/)")
    app = sub.add_parser("update-apply", help="install staged artifact (requires approval)")
    app.add_argument("--version")
    app.add_argument("--artifact", help="basename in the app's dist/ directory")
    app.add_argument("--yes", action="store_true")
    rollback = sub.add_parser("update-rollback", help="restore last update (requires approval)")
    rollback.add_argument("--yes", action="store_true")
    an = sub.add_parser("analytics")
    an.add_argument("--markdown", action="store_true")
    chat = sub.add_parser("chat")
    chat.add_argument("--prompt", required=True)
    chat.add_argument("--chat-id")
    chat.add_argument("--model")
    raw = sub.add_parser("raw", help="send an arbitrary agent command")
    raw.add_argument("name")
    raw.add_argument("--payload", default="{}", help="JSON object with extra fields")

    args = parser.parse_args(argv)
    cfg = _load_config(Path(args.config))
    try:
        result = _run(args, cfg)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
