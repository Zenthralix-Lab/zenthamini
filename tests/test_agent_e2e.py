"""End-to-end coverage: the real CLI process talks to the real agent API over HTTP.

The unit tests stub `_command`, so a wire-format mismatch (like the `shell`
regression) can pass every test while failing on the device. This test boots
the server in-process, enables HMAC signing, and drives it through
`zentha_mini_ctl.py` as a subprocess.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SHARED = os.path.join(ROOT, "Shared")
CLI = os.path.join(SHARED, "scripts", "zentha_mini_ctl.py")
SECRET = "e2e-agent-secret"

sys.path.insert(0, SHARED)

import chat_server


def setup_module():
    td = tempfile.mkdtemp()
    chat_server.SCRIPT_DIR = td
    chat_server.CHATS_DIR = os.path.join(td, "chat_data")
    chat_server.CHATS_FILE = os.path.join(chat_server.CHATS_DIR, "chats.json")
    chat_server.SETTINGS_FILE = os.path.join(chat_server.CHATS_DIR, "settings.json")
    chat_server.AGENT_TOKEN_FILE = os.path.join(chat_server.CHATS_DIR, "agent.token")
    chat_server.HTML_FILE = os.path.join(SHARED, "ZenthaMini.html")
    chat_server.LOG_DIR = os.path.join(td, "logs")
    chat_server.LOG_FILE = os.path.join(chat_server.LOG_DIR, "chat_server.log")
    chat_server.LAN_MODE = False
    chat_server.AUTH_TOKEN = None
    chat_server.AGENT_TOKEN = "e2e-agent-token"
    chat_server.ensure_data_dir()
    with open(chat_server.SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"hermes": {"enabled": False, "secret": SECRET}}, f)
    # Port 0 avoids colliding with the fixed ports used by the other modules.
    srv = chat_server.ThreadedHTTPServer(("127.0.0.1", 0), chat_server.ChatHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    setup_module.server = srv
    setup_module.tmp = td
    setup_module.url = f"http://127.0.0.1:{srv.server_address[1]}"


def teardown_module():
    setup_module.server.shutdown()


def _ctl(cfg, *args):
    return subprocess.run(
        [sys.executable, CLI, "--config", cfg, "--json", *args],
        capture_output=True, text=True, timeout=60,
    )


def _ok(cfg, *args):
    proc = _ctl(cfg, *args)
    assert proc.returncode == 0, f"{' '.join(args)} failed: {proc.stderr.strip()}"
    return json.loads(proc.stdout)


def _setup(secret=True):
    td = setup_module.tmp
    cfg = os.path.join(td, "signed.json" if secret else "unsigned.json")
    args = ["setup", "--url", setup_module.url]
    if secret:
        secret_file = os.path.join(td, "hermes.secret")
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(SECRET + "\n")
        args += ["--secret-file", secret_file]
    assert _ok(cfg, *args)["ok"] is True
    return cfg


def test_setup_writes_private_config():
    cfg = _setup()
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600


def test_signed_cli_runs_status_and_shell():
    cfg = _setup()
    assert _ok(cfg, "id")["install_id"].startswith("zm-")
    status = _ok(cfg, "status")
    assert status["command"] == "maintenance_status" and status["ok"] is True
    shell = _ok(cfg, "shell", "--command", "echo zentha-e2e")
    assert shell["command"] == "shell" and shell["cmd"] == "echo zentha-e2e"
    assert shell["returncode"] == 0 and "zentha-e2e" in shell["stdout"]
    assert _ok(cfg, "shell", "--command", "exit 3")["returncode"] == 3
    assert _ok(cfg, "monitor")["ok"] is True


def test_signed_cli_file_commands_roundtrip():
    cfg = _setup()
    target = os.path.join(setup_module.tmp, "e2e-note.txt")
    _ok(cfg, "write", target, "--content", "hello-e2e", "--yes")
    assert _ok(cfg, "cat", target)["content"] == "hello-e2e"
    assert _ok(cfg, "ls", setup_module.tmp)["ok"] is True
    nested = os.path.join(setup_module.tmp, "e2e-dir")
    _ok(cfg, "mkdir", nested)
    _ok(cfg, "mv", target, os.path.join(nested, "moved.txt"), "--yes")
    assert _ok(cfg, "cat", os.path.join(nested, "moved.txt"))["content"] == "hello-e2e"
    _ok(cfg, "rm", nested, "--yes")
    assert not os.path.exists(nested)


def test_unsigned_cli_cannot_run_shell():
    cfg = _setup(secret=False)
    proc = _ctl(cfg, "shell", "--command", "echo refused")
    assert proc.returncode != 0
    assert "unauthorized" in proc.stderr.lower() or "HMAC" in proc.stderr
