import os, sys, tempfile, json, threading, time, urllib.request, urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SHARED = os.path.join(ROOT, "Shared")
sys.path.insert(0, SHARED)

import chat_server

def setup_module():
    td = tempfile.mkdtemp()
    chat_server.SCRIPT_DIR = td
    chat_server.CHATS_DIR = os.path.join(td, "chat_data")
    chat_server.CHATS_FILE = os.path.join(chat_server.CHATS_DIR, "chats.json")
    chat_server.SETTINGS_FILE = os.path.join(chat_server.CHATS_DIR, "settings.json")
    chat_server.HTML_FILE = os.path.join(SHARED, "ZenthaMini.html")
    chat_server.LOG_DIR = os.path.join(td, "logs")
    chat_server.LOG_FILE = os.path.join(chat_server.LOG_DIR, "chat_server.log")
    chat_server.CHAT_SERVER_PORT = 3399
    chat_server.LAN_MODE = False
    chat_server.AUTH_TOKEN = None
    chat_server.ensure_data_dir()
    # start server
    srv = chat_server.ThreadedHTTPServer(("127.0.0.1", chat_server.CHAT_SERVER_PORT), chat_server.ChatHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(1)
    setup_module.server = srv

def teardown_module():
    setup_module.server.shutdown()

def req(path, method="GET", data=None):
    url = f"http://127.0.0.1:{chat_server.CHAT_SERVER_PORT}{path}"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method)
    if body:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def test_settings_default():
    st, d = req("/api/settings")
    assert st == 200
    assert "memory" in d and "skills" in d and "tools" in d
    assert "plugins" in d and "connectors" in d and "mcp" in d

def test_settings_save_roundtrip():
    st, _ = req("/api/settings", "POST", {"memory": {"enabled": False, "items": ["fact1"]}})
    assert st == 200
    st, d = req("/api/settings")
    assert d["memory"]["enabled"] is False
    assert d["memory"]["items"] == ["fact1"]

def test_chats_save_load():
    payload = [{"id": "1", "title": "t", "msgs": [], "ts": 1, "model": "m", "sys": ""}]
    st, _ = req("/api/chats", "POST", payload)
    assert st == 200
    st, d = req("/api/chats")
    assert st == 200
    assert isinstance(d, list) and len(d) == 1

def test_static_traversal_blocked():
    # chat_data should not be served
    st, _ = req("/chat_data/settings.json")
    assert st in (403, 404)


def test_private_host_check_rejects_public_172():
    """172.0-15.x and 172.32+ are public, not private; the old string match allowed them."""
    assert chat_server._is_private_host("127.0.0.1") is True
    assert chat_server._is_private_host("localhost") is True
    assert chat_server._is_private_host("192.168.1.5") is True
    assert chat_server._is_private_host("10.1.2.3") is True
    assert chat_server._is_private_host("172.16.0.1") is True
    assert chat_server._is_private_host("172.31.255.254") is True
    assert chat_server._is_private_host("172.15.0.1") is False
    assert chat_server._is_private_host("172.32.0.1") is False
    assert chat_server._is_private_host("8.8.8.8") is False

def test_github_plugin_missing():
    st, d = req("/api/github/plugin?repo=octocat/Hello-World")
    # no plugin.json -> 502 or 404
    assert st in (404, 502, 400)
