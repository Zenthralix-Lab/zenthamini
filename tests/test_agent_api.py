"""Agent-API coverage: auth, command routing, and the chat persistence path.

The agent token endpoint is the highest-privilege surface in the app, so it gets its own
tests instead of riding along with the UI tests.
"""
import os, sys, tempfile, json, threading, time, uuid, hashlib, hmac, urllib.request, urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SHARED = os.path.join(ROOT, "Shared")
sys.path.insert(0, SHARED)

import chat_server

PORT = 3400
TOKEN = "test-agent-token"


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
    chat_server.CHAT_SERVER_PORT = PORT
    chat_server.LAN_MODE = False
    chat_server.AUTH_TOKEN = None
    chat_server.AGENT_TOKEN = TOKEN
    chat_server.ensure_data_dir()
    srv = chat_server.ThreadedHTTPServer(("127.0.0.1", PORT), chat_server.ChatHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1)
    setup_module.server = srv


def teardown_module():
    setup_module.server.shutdown()


def agent(name, token=TOKEN, nonce=None, secret_override=None, body_override=None, **fields):
    url = f"http://127.0.0.1:{PORT}/api/agent/command"
    payload = {"command": name, **fields}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Agent-Token", token)
    # A configured hermes secret turns on mandatory HMAC signing. The signature
    # covers the body and a unique nonce so captured requests cannot be replayed
    # or mutated in flight.
    try:
        secret = (chat_server._load_settings_file().get("hermes") or {}).get("secret")
    except Exception:
        secret = None
    if secret_override is not None:
        secret = secret_override
    if secret:
        ts = str(int(time.time()))
        nonce = nonce or uuid.uuid4().hex
        signed_body = body if body_override is None else body_override
        msg = (f"POST:/api/agent/command:{ts}:{nonce}:"
               f"{hashlib.sha256(signed_body).hexdigest()}").encode()
        req.add_header("X-Agent-Timestamp", ts)
        req.add_header("X-Agent-Nonce", nonce)
        req.add_header("X-Agent-Signature", hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_agent_requires_token():
    st, _ = agent("status", token=None)
    assert st == 401


def test_agent_rejects_wrong_token():
    st, _ = agent("status", token="nope")
    assert st == 401


def test_status_ok():
    st, d = agent("run_doctor")
    assert st == 200 and d.get("ok") is True
    assert "platform" in d and "hostname" in d


def test_settings_get_redacts_hermes_secrets():
    chat_server._persist_settings_file({
        "hermes": {"enabled": True, "webhook_url": "https://example.test/h",
                   "secret": "s3cr3t", "agent_token": TOKEN},
        "defaultModel": "m",
    })
    st, d = agent("settings_get")
    assert st == 200
    hermes = d["settings"]["hermes"]
    assert hermes["webhook_url"] == "https://example.test/h"
    assert "secret" not in hermes and "agent_token" not in hermes
    # and the token still authenticates afterwards
    st, _ = agent("status")
    assert st == 200


def test_chats_save_list_delete():
    st, d = agent("chats_save", chats=[{"id": "a", "title": "A", "msgs": [], "ts": 1}])
    assert st == 200 and d["count"] == 1
    st, d = agent("chats_list")
    assert [c["id"] for c in d["chats"]] == ["a"]
    st, d = agent("chat_delete", id="a")
    assert st == 200
    st, d = agent("chats_list")
    assert d["chats"] == []


def test_unknown_command_is_not_ok():
    st, d = agent("definitely-not-a-command")
    assert st == 200 and d["ok"] is False


def _set_default_model(model):
    s = chat_server._load_settings_file()
    s["defaultModel"] = model
    chat_server._persist_settings_file(s)


def test_chat_persists_reply_to_the_named_chat(monkeypatch=None):
    """Regression: the reply used to be written to a detached copy and lost."""
    _set_default_model("m")
    agent("chats_save", chats=[{"id": "c1", "title": "T", "msgs": [], "ts": 1}])
    chat_server._ollama_chat = lambda model, messages, temperature=None: ("balasan", None)
    try:
        st, d = agent("chat", prompt="halo", chat_id="c1", model="m")
        assert st == 200 and d["ok"] is True and d["reply"] == "balasan"
        st, d = agent("chats_get", id="c1")
        roles = [m["role"] for m in d["chat"]["msgs"]]
        assert roles == ["user", "assistant"], roles
        assert d["chat"]["msgs"][1]["content"] == "balasan"
    finally:
        del chat_server._ollama_chat


def test_chat_does_not_duplicate_user_message():
    _set_default_model("m")
    agent("chats_save", chats=[{"id": "c2", "title": "T", "ts": 1,
                                "msgs": [{"role": "user", "content": "halo"}]}])
    chat_server._ollama_chat = lambda model, messages, temperature=None: ("balasan", None)
    try:
        agent("chat", prompt="halo", chat_id="c2", model="m")
        st, d = agent("chats_get", id="c2")
        users = [m for m in d["chat"]["msgs"] if m["role"] == "user"]
        assert len(users) == 1, d["chat"]["msgs"]
    finally:
        del chat_server._ollama_chat


def test_permissions_policy_allows_own_camera_and_mic():
    """The UI has camera/mic/location buttons; the header must not disable them."""
    url = f"http://127.0.0.1:{PORT}/"
    with urllib.request.urlopen(url, timeout=10) as resp:
        policy = resp.headers.get("Permissions-Policy", "")
    assert "camera=(self)" in policy
    assert "microphone=(self)" in policy


def test_hmac_rejects_replayed_nonce():
    nonce = "fixed-nonce-for-replay-test"
    st, _ = agent("status", nonce=nonce)
    assert st == 200
    st, d = agent("status", nonce=nonce)
    assert st == 401 and d.get("error") == "unauthorized"


def test_hmac_rejects_body_tampering():
    # The signature is computed over a different body than the one sent.
    st, d = agent("status", body_override=b'{"command":"check_update"}')
    assert st == 401 and d.get("error") == "unauthorized"


def test_hmac_rejects_wrong_secret():
    st, d = agent("status", secret_override="not-the-secret")
    assert st == 401 and d.get("error") == "unauthorized"


def test_token_endpoint_returns_effective_configured_token():
    configured = "configured-agent-token"
    s = chat_server._load_settings_file()
    s.setdefault("hermes", {})["agent_token"] = configured
    chat_server._persist_settings_file(s)
    try:
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_hash = hashlib.sha256(b"").hexdigest()
        msg = f"GET:/api/agent/token:{ts}:{nonce}:{body_hash}".encode()
        secret = s["hermes"]["secret"]
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/agent/token", method="GET")
        req.add_header("X-Agent-Token", configured)
        req.add_header("X-Agent-Timestamp", ts)
        req.add_header("X-Agent-Nonce", nonce)
        req.add_header("X-Agent-Signature", hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest())
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read() or b"{}")
        assert d.get("token") == configured
    finally:
        s = chat_server._load_settings_file()
        (s.get("hermes") or {}).pop("agent_token", None)
        chat_server._persist_settings_file(s)


def test_settings_set_cannot_overwrite_password_hash():
    s = chat_server._load_settings_file()
    s["auth_password_hash"] = chat_server._hash_password("keepme")
    chat_server._persist_settings_file(s)
    before = s["auth_password_hash"]
    st, d = agent("settings_set", settings={"auth_password_hash": "sha256:evil",
                                            "defaultModel": "m2"})
    assert st == 200 and d["ok"] is False
    st, d = agent("settings_set", settings={"defaultModel": "m2"})
    assert st == 200 and d["ok"] is True
    # The secret survives a partial Hermes patch.
    st, d = agent("settings_set", settings={"hermes": {"enabled": False}})
    assert st == 200 and d["ok"] is True
    assert (chat_server._load_settings_file().get("hermes") or {}).get("secret") == "s3cr3t"
    assert chat_server._load_settings_file().get("auth_password_hash") == before


def test_oversized_agent_body_is_rejected():
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    conn.putrequest("POST", "/api/agent/command")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(chat_server.BODY_LIMITS["/api/agent/command"] + 1))
    conn.putheader("X-Agent-Token", TOKEN)
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 413
    resp.read()
    conn.close()


def test_logout_removes_server_side_session():
    chat_server._AUTH_HASH = chat_server._hash_password("test-password")
    body = json.dumps({"password": "test-password"}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/login", data=body,
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        sid = (resp.headers.get("Set-Cookie") or "").split("zm_session=", 1)[1].split(";", 1)[0]
    assert sid in chat_server._SESSIONS
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/logout", data=b"{}",
                                 method="POST", headers={"Cookie": f"zm_session={sid}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert json.loads(resp.read() or b"{}").get("ok") is True
    assert sid not in chat_server._SESSIONS


def test_install_id_is_stable_and_well_formed():
    first = chat_server._get_or_create_install_id()
    second = chat_server._get_or_create_install_id()
    assert first == second
    assert first.startswith("zm-")
    assert len(first) == 19
    # It is not the agent token: identity and credential stay separate.
    assert first != chat_server._effective_agent_token()


def test_agent_status_includes_install_id():
    st, d = agent("run_doctor")
    assert st == 200
    assert d.get("install_id", "").startswith("zm-")


def test_privileged_control_requires_signed_agent():
    """A bare token must not be able to run shell commands; only the HMAC signer may."""
    s = chat_server._load_settings_file()
    s.setdefault("hermes", {})
    s["hermes"].pop("secret", None)
    s["hermes"].pop("agent_token", None)
    chat_server._persist_settings_file(s)
    st, d = agent("shell", cmd="echo hi")
    assert st == 200 and d["ok"] is False
    assert "HMAC" in (d.get("error") or "")
    st, d = agent("monitor")
    assert st == 200 and d["ok"] is False
    assert "HMAC" in (d.get("error") or "")


def test_signed_agent_can_run_shell_and_read_install_id():
    secret = "control-test-secret"
    s = chat_server._load_settings_file()
    s.setdefault("hermes", {})["secret"] = secret
    chat_server._persist_settings_file(s)
    try:
        st, d = agent("install_id")
        assert st == 200 and d["install_id"].startswith("zm-")
        cmd = f'"{sys.executable}" -c "print(\'zentha-ok\')"'
        st, d = agent("shell", cmd=cmd, timeout=15)
        assert st == 200 and d["ok"] is True
        assert d["command"] == "shell" and d["cmd"] == cmd
        assert d["returncode"] == 0 and "zentha-ok" in d["stdout"]
        st, d = agent("fs_list", path=".")
        assert st == 200 and d["ok"] is True and isinstance(d["entries"], list)
    finally:
        s = chat_server._load_settings_file()
        (s.get("hermes") or {}).pop("secret", None)
        chat_server._persist_settings_file(s)


def _make_release(root):
    """Build a minimal verified release artifact in a throwaway project root."""
    import io, tarfile
    root = root if isinstance(root, str) else str(root)
    dist = os.path.join(root, "dist")
    os.makedirs(dist, exist_ok=True)
    name = "zenthmini_9.9.9.tar.gz"
    files = {"VERSION": b"9.9.9\n", "README.md": b"release\n",
             "Shared/chat_server.py": b"print('upgraded')\n"}
    with tarfile.open(os.path.join(dist, name), "w:gz") as tf:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    import hashlib
    digest = hashlib.sha256(open(os.path.join(dist, name), "rb").read()).hexdigest()
    with open(os.path.join(dist, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write(f"{digest}  {name}\n")
    return name


def test_maintenance_commands_through_agent_api(tmp_path):
    root = str(tmp_path / "root")
    os.makedirs(os.path.join(root, "Shared"), exist_ok=True)
    with open(os.path.join(root, "VERSION"), "w", encoding="utf-8") as f:
        f.write("1.0.1\n")
    name = _make_release(root)
    old_root = chat_server.APP_ROOT
    chat_server.APP_ROOT = root
    try:
        st, d = agent("maintenance_status")
        assert st == 200 and d["ok"] is True and d["version"] == "1.0.1"
        st, d = agent("update_status")
        assert st == 200 and d["ok"] is True
        assert any(a["artifact"] == name for a in d["artifacts"])
        st, d = agent("update_prepare", artifact=name)
        assert st == 200 and d["ok"] is True and d["version"] == "9.9.9"
        # Apply and rollback are gated by confirm:true.
        st, d = agent("update_apply")
        assert st == 200 and d["ok"] is False and "confirm" in d["error"]
        st, d = agent("update_apply", confirm=True)
        assert st == 200 and d["ok"] is True and d["files_updated"] == 3
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            assert f.read().strip() == "9.9.9"
        st, d = agent("update_rollback", confirm=True)
        assert st == 200 and d["ok"] is True and d["restored"] == 1
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            assert f.read().strip() == "1.0.1"
    finally:
        chat_server.APP_ROOT = old_root
