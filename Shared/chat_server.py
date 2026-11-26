#!/usr/bin/env python3
#     ███████╗███████╗███╗   ██╗████████╗██╗  ██╗██████╗  █████╗ ██╗     ██╗██╗  ██╗
#     ╚══███╔╝██╔════╝████╗  ██║╚══██╔══╝██║  ██║██╔══██╗██╔══██╗██║     ██║╚██╗██╔╝
#       ███╔╝ █████╗  ██╔██╗ ██║   ██║   ███████║██████╔╝███████║██║     ██║ ╚███╔╝
#      ███╔╝  ██╔══╝  ██║╚██╗██║   ██║   ██╔══██║██╔══██╗██╔══██║██║     ██║ ██╔██╗
#     ███████╗███████╗██║ ╚████║   ██║   ██║  ██║██║  ██║██║  ██║███████╗██║██╔╝ ██╗
#     ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝╚═╝  ╚═╝

"""
ZenthaMini Chat Server
=======================
A zero-dependency Python HTTP server that:
  1. Serves the ZenthaMini.html web interface
  2. Saves/loads chat history as JSON files on the USB drive
  3. Proxies all Ollama API requests (eliminates CORS issues)

Works on Windows, macOS, and Linux without installing anything.
"""

import http.server
import json
import os
import sys
import urllib.request
import urllib.error
import threading
import webbrowser
import time
import ssl
import platform
import ctypes
import logging
import logging.handlers
import queue
import uuid
import subprocess
import hashlib
import hmac
import ipaddress
import re
import shutil
from datetime import datetime
from urllib.parse import urlparse

# Optional: psutil for hardware stats (graceful fallback to native APIs if not installed)
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ── Configuration ──────────────────────────────────────────────
CHAT_SERVER_PORT = 3333
OLLAMA_HOST = "http://127.0.0.1:11434"
LLAMA_CPP_MODE = "--llama-cpp" in sys.argv
if LLAMA_CPP_MODE:
    OLLAMA_HOST = "http://127.0.0.1:8080"

LAN_MODE = "--lan" in sys.argv
HTTPS_MODE = "--https" in sys.argv
NO_BROWSER = "--no-browser" in sys.argv


def _parse_port(argv, default):
    """Accept ``--port N`` / ``--port=N`` so a second instance can be smoke-tested
    without taking the single-instance port 3333 from the running app."""
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return default
        if a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return default
    return default


CHAT_SERVER_PORT = _parse_port(sys.argv, CHAT_SERVER_PORT)

# Always resolve paths relative to THIS script's location (the USB drive)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHATS_DIR = os.path.join(SCRIPT_DIR, "chat_data")
CHATS_FILE = os.path.join(CHATS_DIR, "chats.json")
SETTINGS_FILE = os.path.join(CHATS_DIR, "settings.json")
AUTH_TOKEN_FILE = os.path.join(CHATS_DIR, "auth.token")


def _chmod_secure(path):
    """Best-effort 0600 on files holding secrets/tokens."""
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _get_or_create_token():
    os.makedirs(CHATS_DIR, exist_ok=True)
    try:
        with open(AUTH_TOKEN_FILE, "r", encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t
    except Exception:
        pass
    t = uuid.uuid4().hex
    with open(AUTH_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(t)
    _chmod_secure(AUTH_TOKEN_FILE)
    return t

AUTH_TOKEN = _get_or_create_token() if LAN_MODE else None

# Analytics lives beside this file; make that importable no matter where we were launched from.
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
try:
    import analytics as zentha_analytics
except Exception:  # analytics is an enhancement; the server must still boot without it
    zentha_analytics = None
try:
    import maintenance as zentha_maintenance
except Exception:  # maintenance is optional at runtime; agent commands report the error
    zentha_maintenance = None


def _app_root():
    """Project root for a portable install (this file normally lives in Shared/)."""
    parent = os.path.dirname(SCRIPT_DIR)
    return parent if os.path.basename(SCRIPT_DIR) == "Shared" else SCRIPT_DIR


APP_ROOT = _app_root()
SERVER_START_TIME = time.time()
AGENT_NONCE_WINDOW = 300
AGENT_NONCES = {}
AGENT_NONCES_LOCK = threading.Lock()
CPU_SAMPLE_LOCK = threading.Lock()
LOGIN_FAILURES = {}
LOGIN_LOCK = threading.Lock()
MAX_BODY_BYTES = 10 * 1024 * 1024
BODY_LIMITS = {
    "/api/agent/command": 2 * 1024 * 1024,
    "/api/settings": 1024 * 1024,
    "/api/telemetry": 1024 * 1024,
    "/api/login": 4096,
}

AGENT_TOKEN_FILE = os.path.join(CHATS_DIR, "agent.token")
def _get_or_create_agent_token():
    os.makedirs(CHATS_DIR, exist_ok=True)
    try:
        with open(AGENT_TOKEN_FILE, "r", encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t
    except Exception:
        pass
    t = "zm_agent_" + uuid.uuid4().hex
    with open(AGENT_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(t)
    _chmod_secure(AGENT_TOKEN_FILE)
    return t
AGENT_TOKEN = _get_or_create_agent_token()

# ── Unique install fingerprint ─────────────────────────────────
# Every ZenthaMini installation gets a stable, unique identity so Hermes can tell
# devices apart in a fleet. It survives restarts and model/settings changes, and
# is independent of the hostname (which can collide or change).
INSTALL_ID_FILE = os.path.join(CHATS_DIR, "install.id")
INSTALL_ID_RE = re.compile(r"^zm-[0-9a-f]{16}$")


def _get_or_create_install_id():
    """Return this install's stable ID, creating it once on first run."""
    os.makedirs(CHATS_DIR, exist_ok=True)
    try:
        with open(INSTALL_ID_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if INSTALL_ID_RE.match(value):
            return value
    except Exception:
        pass
    value = "zm-" + uuid.uuid4().hex[:16]
    with open(INSTALL_ID_FILE, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    _chmod_secure(INSTALL_ID_FILE)
    return value


def _install_fingerprint():
    """Public identity bundled into status/pushes: install ID + non-secret traits."""
    return {
        "install_id": _get_or_create_install_id(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "hostname": platform.node(),
    }


def _effective_agent_token():
    """The configured Hermes token wins over the locally generated fallback."""
    try:
        configured = (_load_settings_file().get("hermes") or {}).get("agent_token")
        if configured:
            return configured
    except Exception:
        pass
    return AGENT_TOKEN


UI_SETTINGS_KEYS = {
    "globalSystemPrompt", "temperature", "logMode", "memory", "skills", "tools",
    "plugins", "connectors", "mcp", "supportUrl", "websiteUrl", "defaultModel",
    "telemetry", "improveModelForEveryone",
}


def _redact_settings(settings):
    """Never expose auth material through a settings read endpoint."""
    safe = dict(settings or {})
    safe.pop("auth_password_hash", None)
    hermes = safe.get("hermes")
    if isinstance(hermes, dict):
        safe["hermes"] = {k: v for k, v in hermes.items()
                          if k not in ("secret", "agent_token")}
    return safe


def _read_version():
    for cand in (os.path.join(APP_ROOT, "VERSION"),
                 os.path.join(SCRIPT_DIR, "VERSION"),
                 os.path.join(SCRIPT_DIR, "..", "VERSION")):
        try:
            with open(cand, "r", encoding="utf-8") as f:
                value = f.read().strip()
            if value:
                return value
        except Exception:
            continue
    return "1.0.0"


def _version_order(value):
    """Numeric comparison: 1.10.0 is newer than 1.9.0."""
    return tuple(int(x) for x in re.findall(r"\d+", value or ""))


def _update_check_data():
    current = _read_version()
    latest = current
    available = False
    error = None
    manifest_url = "https://raw.githubusercontent.com/Zenthralix/ZenthaMini/main/VERSION"
    try:
        req = urllib.request.Request(manifest_url, headers={"User-Agent": "ZenthaMini"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            latest = resp.read().decode("utf-8").strip()
        available = bool(latest) and _version_order(latest) > _version_order(current)
    except Exception as exc:
        error = str(exc)
    return {
        "current": current,
        "latest": latest,
        "available": available,
        "release_url": "https://github.com/Zenthralix/ZenthaMini/releases",
        "changelog": "",
        "error": error,
    }


import threading as _t
_SESSIONS = {}
_SESSIONS_LOCK = _t.Lock()

def _hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 100000)
    return "pbkdf2:sha256:100000:" + salt.hex() + ":" + h.hex()

def _verify_password(pw, stored):
    if not stored:
        return False
    if stored.startswith("pbkdf2:"):
        try:
            parts = stored.split(":")
            # pbkdf2:sha256:iters:salt:hash
            iters = int(parts[2])
            salt = bytes.fromhex(parts[3])
            h = parts[4]
            calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters).hex()
            import hmac as _h
            return _h.compare_digest(calc, h)
        except Exception:
            return False
    # legacy sha256
    return hashlib.sha256(pw.encode("utf-8")).hexdigest() == stored

def _get_or_create_password():
    s = _load_settings_file()
    h = s.get("auth_password_hash")
    if h:
        return h, None
    pw = uuid.uuid4().hex[:12]
    h = _hash_password(pw)
    s["auth_password_hash"] = h
    _persist_settings_file(s)
    return h, pw

_AUTH_HASH = None
_AUTH_PLAIN = None

def _ensure_password():
    global _AUTH_HASH, _AUTH_PLAIN
    if _AUTH_HASH is not None:
        return
    h, pw = _get_or_create_password()
    _AUTH_HASH = h
    _AUTH_PLAIN = pw


def _load_soul_text():
    for cand in (os.path.join(SCRIPT_DIR, "SOUL.md"), os.path.join(SCRIPT_DIR, "..", "SOUL.md")):
        try:
            with open(cand, "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                return txt
        except Exception:
            continue
    return ""
HTML_FILE = os.path.join(SCRIPT_DIR, "ZenthaMini.html")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "chat_server.log")
LOG_MODE_ERRORS_ONLY = "errors_only"
LOG_MODE_ALL = "all"
DEFAULT_LOG_MODE = LOG_MODE_ERRORS_ONLY

# Lock shared file persistence paths to avoid concurrent write corruption.
DATA_FILE_LOCK = threading.RLock()

# Simple in-memory rate limiter
_RATE_LIMIT = {}
_RATE_LOCK = threading.Lock()
def _rate_limit_ok(ip, limit=120, window=60):
    import time as _t
    now = _t.time()
    with _RATE_LOCK:
        bucket = _RATE_LIMIT.get(ip)
        if not bucket or now - bucket[0] > window:
            _RATE_LIMIT[ip] = [now, 1]
            return True
        if bucket[1] >= limit:
            return False
        bucket[1] += 1
        return True

def _login_throttled(ip):
    """Five failed password attempts per IP in five minutes -> temporary lockout."""
    now = time.time()
    with LOGIN_LOCK:
        for key, value in list(LOGIN_FAILURES.items()):
            if now - value[0] > 300:
                LOGIN_FAILURES.pop(key, None)
        rec = LOGIN_FAILURES.get(ip)
        return bool(rec and rec[1] >= 5)


def _login_failed(ip):
    with LOGIN_LOCK:
        first, count = LOGIN_FAILURES.get(ip, (time.time(), 0))
        LOGIN_FAILURES[ip] = (first, count + 1)


def _login_succeeded(ip):
    with LOGIN_LOCK:
        LOGIN_FAILURES.pop(ip, None)


# Agent-token brute force: five bad tokens from one IP in five minutes -> lockout.
AGENT_FAILURES = {}
AGENT_FAILURES_LOCK = threading.Lock()


def _agent_throttled(ip):
    now = time.time()
    with AGENT_FAILURES_LOCK:
        for key, value in list(AGENT_FAILURES.items()):
            if now - value[0] > 300:
                AGENT_FAILURES.pop(key, None)
        rec = AGENT_FAILURES.get(ip)
        return bool(rec and rec[1] >= 5)


def _agent_failed(ip):
    with AGENT_FAILURES_LOCK:
        first, count = AGENT_FAILURES.get(ip, (time.time(), 0))
        AGENT_FAILURES[ip] = (first, count + 1)


def _agent_succeeded(ip):
    with AGENT_FAILURES_LOCK:
        AGENT_FAILURES.pop(ip, None)


LOG_MODE_LOCK = threading.RLock()
ACTIVE_LOG_MODE = DEFAULT_LOG_MODE

# ── Pure-Python Hardware Stats (no psutil needed) ──────────────
_cpu_times_last = None  # (idle, total) from previous sample

def _get_hw_stats():
    """Thread-safe wrapper: CPU sampling uses shared delta state."""
    with CPU_SAMPLE_LOCK:
        return _get_hw_stats_unlocked()


def _get_hw_stats_unlocked():
    """Return (cpu_percent, ram_percent) using only stdlib / ctypes."""
    global _cpu_times_last  # must be at top of function, before any branch uses it

    if HAS_PSUTIL:
        cpu = round(psutil.cpu_percent(interval=0), 1)
        ram = round(psutil.virtual_memory().percent, 1)
        return cpu, ram

    plat = platform.system()

    # ── Windows ──────────────────────────────────────────────────
    if plat == "Windows":
        # RAM via GlobalMemoryStatusEx
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength",                ctypes.c_ulong),
                ("dwMemoryLoad",            ctypes.c_ulong),
                ("ullTotalPhys",            ctypes.c_ulonglong),
                ("ullAvailPhys",            ctypes.c_ulonglong),
                ("ullTotalPageFile",        ctypes.c_ulonglong),
                ("ullAvailPageFile",        ctypes.c_ulonglong),
                ("ullTotalVirtual",         ctypes.c_ulonglong),
                ("ullAvailVirtual",         ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        msx = MEMORYSTATUSEX()
        msx.dwLength = ctypes.sizeof(msx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(msx))
        ram = float(msx.dwMemoryLoad)

        # CPU via GetSystemTimes (idle/kernel/user tick counts)
        FILETIME = ctypes.c_ulonglong
        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        idle_v = idle.value
        total_v = kern.value + user.value
        if _cpu_times_last is None:
            # First call — sleep briefly and sample again
            time.sleep(0.25)
            idle2, kern2, user2 = FILETIME(), FILETIME(), FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle2), ctypes.byref(kern2), ctypes.byref(user2))
            d_idle  = idle2.value - idle_v
            d_total = (kern2.value + user2.value) - total_v
            _cpu_times_last = (idle2.value, kern2.value + user2.value)
        else:
            prev_idle, prev_total = _cpu_times_last
            d_idle  = idle_v  - prev_idle
            d_total = total_v - prev_total
            _cpu_times_last = (idle_v, total_v)

        cpu = round((1.0 - d_idle / max(d_total, 1)) * 100.0, 1)
        cpu = max(0.0, min(100.0, cpu))
        return cpu, ram

    # ── Linux ─────────────────────────────────────────────────────
    elif plat == "Linux":
        # RAM
        ram = 0.0
        try:
            with open("/proc/meminfo") as f:
                mem = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(":")] = int(parts[1])
            total = mem.get("MemTotal", 1)
            avail = mem.get("MemAvailable", total)
            ram = round((1 - avail / total) * 100, 1)
        except Exception:
            pass
        # CPU via /proc/stat delta
        cpu = 0.0
        try:
            def read_cpu():
                with open("/proc/stat") as f:
                    parts = f.readline().split()
                vals = [int(x) for x in parts[1:]]
                idle = vals[3]
                total = sum(vals)
                return idle, total
            if _cpu_times_last is None:
                i1, t1 = read_cpu()
                time.sleep(0.25)
                i2, t2 = read_cpu()
            else:
                i1, t1 = _cpu_times_last
                i2, t2 = read_cpu()
            _cpu_times_last = (i2, t2)
            d_idle  = i2 - i1
            d_total = t2 - t1
            cpu = round((1 - d_idle / max(d_total, 1)) * 100, 1)
        except Exception:
            pass
        return cpu, ram

    # ── macOS ─────────────────────────────────────────────────────
    else:
        # User requested to skip macOS usage to avoid any potential permission/execution issues
        cpu = 0.0
        ram = 0.0
        return cpu, ram


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_private_host(host):
    """True only for loopback or RFC1918 / RFC4193 private addresses.

    A bare ``startswith("172.")`` also matches the public 172.16.0.0/12-adjacent
    ranges (172.0-15 and 172.32-255), so parse the address instead of string-matching.
    """
    host = (host or "").strip()
    if host in ("localhost", ""):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def _is_loopback_addr(host):
    host = (host or "").strip()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _agent_secret():
    """HMAC secret used to sign agent traffic in both directions."""
    try:
        return ((_load_settings_file().get("hermes") or {}).get("secret") or "")
    except Exception:
        return ""


def _agent_sig_headers(method, path, ts, nonce, body, secret):
    """Build the HMAC-SHA256 signature over method, path, timestamp, nonce and body."""
    body_hash = hashlib.sha256(body or b"").hexdigest()
    msg = ("%s:%s:%s:%s:%s" % (method, path, ts, nonce, body_hash)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _normalize_log_mode(value):
    if value == LOG_MODE_ALL:
        return LOG_MODE_ALL
    return LOG_MODE_ERRORS_ONLY

def _is_log_enabled(level):
    with LOG_MODE_LOCK:
        mode = ACTIVE_LOG_MODE
    if mode == LOG_MODE_ALL:
        return True
    return level >= logging.ERROR

def _load_settings_file():
    _soul = _load_soul_text()
    default_settings = {
        "globalSystemPrompt": _soul,
        "temperature": 0.7,
        "logMode": DEFAULT_LOG_MODE,
        "memory": {"enabled": True, "items": []},
        "skills": {"enabled": True, "items": []},
        "tools": {"enabled": True, "items": []},
        "plugins": {"enabled": True, "items": []},
        "connectors": {"enabled": True, "items": []},
        "mcp": {"enabled": True, "servers": []},
        "supportUrl": "https://saweria.co/ZenthralixLab",
        "websiteUrl": "https://zenthralix.vercel.app",
        "hermes": {"enabled": False, "webhook_url": "", "secret": "", "agent_token": ""},
        "auth_password_hash": "",
        "telemetry": False,
        "improveModelForEveryone": False,
    }
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            merged = dict(default_settings)
            merged.update(loaded)
            merged["logMode"] = _normalize_log_mode(merged.get("logMode"))
            for k in ("memory", "skills", "tools", "plugins", "connectors", "mcp", "hermes"):
                if not isinstance(merged.get(k), dict):
                    merged[k] = default_settings[k]
            if not merged.get("auth_password_hash"):
                merged["auth_password_hash"] = default_settings.get("auth_password_hash", "")
            if not isinstance(merged.get("telemetry"), bool):
                merged["telemetry"] = bool(default_settings.get("telemetry", False))
            if not isinstance(merged.get("improveModelForEveryone"), bool):
                merged["improveModelForEveryone"] = bool(default_settings.get("improveModelForEveryone", False))
            return merged
    except Exception:
        pass
    return default_settings

def _persist_settings_file(settings):
    with DATA_FILE_LOCK:
        temp_file = SETTINGS_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.flush()
        os.replace(temp_file, SETTINGS_FILE)
        _chmod_secure(SETTINGS_FILE)

def _set_active_log_mode(mode):
    global ACTIVE_LOG_MODE
    with LOG_MODE_LOCK:
        ACTIVE_LOG_MODE = _normalize_log_mode(mode)

def _get_hardware_specs():
    """Collect a stable host hardware snapshot for log enrichment."""
    plat = platform.system()
    specs = {
        "platform": plat,
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python_version": sys.version.split()[0],
        "cpu_count_logical": os.cpu_count() or 0,
        "has_psutil": HAS_PSUTIL,
        "ram_total_gb": None,
    }
    if HAS_PSUTIL:
        try:
            total_ram = psutil.virtual_memory().total
            specs["ram_total_gb"] = round(total_ram / (1024 ** 3), 2)
        except Exception:
            pass
    elif plat == "Windows":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            msx = MEMORYSTATUSEX()
            msx.dwLength = ctypes.sizeof(msx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(msx))
            specs["ram_total_gb"] = round(msx.ullTotalPhys / (1024 ** 3), 2)
        except Exception:
            pass
    elif plat == "Linux":
        try:
            mem = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(":")] = int(parts[1])
            total_kb = mem.get("MemTotal")
            if total_kb:
                specs["ram_total_gb"] = round((total_kb * 1024) / (1024 ** 3), 2)
        except Exception:
            pass
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        _, value = line.split(":", 1)
                        model = value.strip()
                        if model:
                            specs["processor"] = model
                            break
        except Exception:
            pass
    else:
        # Keep parity with _get_hw_stats fallback behavior:
        # skip macOS/native probing in the non-psutil path.
        pass
    return specs

# ── Remote agent primitives ────────────────────────────────────
# Full-access surface for the Hermes-side agent (Zentha). Everything the web UI can do is
# reachable here through /api/agent/command with the agent token.
VOICE_DIR = os.path.join(CHATS_DIR, "voice")


def _ensure_voice_dir():
    os.makedirs(VOICE_DIR, exist_ok=True)


def _ollama_chat(model, messages, temperature=None, timeout=300):
    """Non-streaming completion from the local engine. Returns (reply, error)."""
    settings = _load_settings_file()
    temp = settings.get("temperature", 0.7) if temperature is None else temperature
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": float(temp)},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(OLLAMA_HOST + "/api/chat", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    return (body.get("message", {}) or {}).get("content", ""), None


def _tts_ogg(text, lang="id"):
    """Text -> OGG/Opus voice note on disk. Returns the absolute path."""
    import tempfile
    _ensure_voice_dir()
    from gtts import gTTS
    fd, mp3 = tempfile.mkstemp(suffix=".mp3", dir=VOICE_DIR)
    os.close(fd)
    ogg = os.path.join(VOICE_DIR, "voice_%d.ogg" % int(time.time() * 1000))
    try:
        gTTS(text=text, lang=lang).save(mp3)
        subprocess.run(["ffmpeg", "-y", "-i", mp3, "-ar", "48000", "-ac", "1",
                        "-c:a", "libopus", ogg], check=True, capture_output=True)
    finally:
        try:
            os.unlink(mp3)
        except OSError:
            pass
    return ogg


def _stt_ogg(path):
    """Voice note -> text with the local whisper model. Returns (text, error).

    Decode with ffmpeg into raw f32 PCM first: the PyAV build on some hosts rejects the
    ``metadata_errors`` argument faster-whisper passes to ``av.open``, so feeding the
    decoder a numpy array keeps transcription working there.
    """
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except Exception as exc:
        return "", "stt unavailable: %s" % exc
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ar", "16000", "-ac", "1", "-f", "f32le", "-"],
        capture_output=True, timeout=180)
    if proc.returncode != 0:
        return "", (proc.stderr or b"ffmpeg failed").decode("utf-8", "replace")[:200]
    samples = np.frombuffer(proc.stdout, dtype=np.float32)
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(samples)
    return " ".join(s.text for s in segments).strip(), None


def _load_chats_file():
    try:
        with open(CHATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ── Device intelligence (Palantir-style fusion) ─────────────────
# Every reader is local-only: nothing here leaves the device. Sensitive readers (browser data,
# camera, mic, location) are explicit commands and every call is written to the audit log.
INTEL_LOG = os.path.join(LOG_DIR, "intel-access.log")
SENSITIVE_COMMANDS = {"browser_data", "camera_snap", "mic_record", "location_get", "intel", "analytics"}
PUSH_REDACTED_COMMANDS = SENSITIVE_COMMANDS | {"chat", "voice_chat", "stt"}


def _intel_log(command, detail="", client_ip="unknown"):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(INTEL_LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\t%s\n" % (
                datetime.now().astimezone().isoformat(), client_ip, command, detail))
            f.flush()
    except Exception:
        pass


def _run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


# ── Full-access control primitives (Hermes / agent) ────────────
# These give the authorised agent the same reach a human has at the keyboard:
# run commands, manage files and processes, control the engine and the machine.
# Every call is gated by the agent token (+ optional HMAC) and written to the
# intel audit log for sensitive/destructive operations.
SHELL_MAX_OUTPUT = 20000
CONTROL_COMMANDS = {
    "shell", "fs_list", "fs_read", "fs_write", "fs_delete", "fs_mkdir",
    "fs_move", "process_list", "process_kill", "engine_start", "engine_stop",
    "engine_status", "server_restart", "power", "monitor", "install_id",
}
# Monitoring that exposes device internals is reserved for the signed Hermes agent.
PRIVILEGED_MONITOR_COMMANDS = {
    "monitor", "device_info", "network_info", "ports", "browser_data",
    "camera_snap", "mic_record", "location_get", "intel", "analytics", "telemetry_get",
}


def _truncate(text, limit=SHELL_MAX_OUTPUT):
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def _run_shell(command, timeout=30, cwd=None):
    """Run a shell command on the host (full device control surface)."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command is required")
    try:
        timeout = max(1, min(int(timeout or 30), 600))
    except (TypeError, ValueError):
        timeout = 30
    started = time.time()
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd or None, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"cmd": command, "returncode": -1, "timed_out": True,
                "stdout": "", "stderr": "timed out after %ss" % timeout,
                "duration_ms": int((time.time() - started) * 1000)}
    return {
        "cmd": command,
        "returncode": proc.returncode,
        "stdout": _truncate(proc.stdout),
        "stderr": _truncate(proc.stderr),
        "duration_ms": int((time.time() - started) * 1000),
        "cwd": cwd or os.getcwd(),
    }


def _fs_path(path):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")
    return os.path.abspath(os.path.expanduser(path))


def _fs_list(path=".", limit=500):
    target = _fs_path(path)
    if not os.path.isdir(target):
        raise ValueError("not a directory: %s" % target)
    entries = []
    try:
        names = sorted(os.listdir(target))[: max(1, int(limit or 500))]
    except OSError as exc:
        raise ValueError("cannot list %s: %s" % (target, exc))
    for name in names:
        full = os.path.join(target, name)
        try:
            st = os.stat(full)
            entries.append({"name": name, "path": full,
                            "type": "dir" if os.path.isdir(full) else "file",
                            "size": st.st_size, "mode": oct(st.st_mode & 0o777),
                            "mtime": int(st.st_mtime)})
        except OSError:
            entries.append({"name": name, "path": full, "type": "unknown"})
    return {"path": target, "entries": entries}


def _fs_read(path, max_bytes=200000):
    target = _fs_path(path)
    with open(target, "rb") as f:
        data = f.read(max(1, min(int(max_bytes or 200000), 5000000)))
    return {"path": target, "content": data.decode("utf-8", "replace"), "bytes": len(data)}


def _fs_write(path, content, append=False):
    target = _fs_path(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "a" if append else "w", encoding="utf-8") as f:
        f.write("" if content is None else str(content))
    return {"path": target, "bytes": os.path.getsize(target), "append": bool(append)}


def _fs_delete(path):
    target = _fs_path(path)
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)
        return {"path": target, "deleted": "directory"}
    os.unlink(target)
    return {"path": target, "deleted": "file"}


def _fs_move(src, dst):
    s, d = _fs_path(src), _fs_path(dst)
    parent = os.path.dirname(d)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.move(s, d)
    return {"from": s, "to": d}


def _process_list(limit=200):
    procs = []
    if HAS_PSUTIL:
        try:
            for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent"]):
                procs.append(p.info)
        except Exception:
            procs = []
    elif platform.system() == "Windows":
        for line in _run(["tasklist", "/fo", "csv", "/nh"]).splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                procs.append({"name": parts[0], "pid": parts[1]})
    else:
        for line in _run(["ps", "-eo", "pid,user,pcpu,pmem,comm", "--no-headers"]).splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 5:
                procs.append({"pid": parts[0], "user": parts[1], "cpu_percent": parts[2],
                              "memory_percent": parts[3], "name": parts[4]})
    return {"count": len(procs), "processes": procs[: max(1, int(limit or 200))]}


def _process_kill(pid=None, name=None, all_matches=False):
    killed = []
    if pid is None and not name:
        raise ValueError("pid or name is required")
    if HAS_PSUTIL:
        targets = []
        for p in psutil.process_iter(["pid", "name"]):
            info = p.info
            if pid is not None and str(info.get("pid")) == str(pid):
                targets.append(p)
            elif name and (info.get("name") or "").lower() == str(name).lower():
                targets.append(p)
        for p in (targets if all_matches else targets[:1]):
            try:
                p.terminate()
                killed.append({"pid": p.info.get("pid"), "name": p.info.get("name")})
            except Exception:
                pass
    elif pid is not None:
        cmd = (["taskkill", "/PID", str(pid), "/F"] if platform.system() == "Windows"
               else ["kill", "-9", str(pid)])
        subprocess.run(cmd, capture_output=True)
        killed.append({"pid": pid})
    else:
        cmd = (["taskkill", "/IM", str(name), "/F"] if platform.system() == "Windows"
               else ["pkill", "-9", "-x", str(name)])
        subprocess.run(cmd, capture_output=True)
        killed.append({"name": name})
    return {"killed": killed}


def _engine_binary():
    bindir = os.path.join(SCRIPT_DIR, "bin")
    for name in ("ollama-windows.exe", "ollama-linux", "ollama-darwin", "llama-server-android"):
        cand = os.path.join(bindir, name)
        if os.path.isfile(cand):
            return cand
    return None


def _engine_is_up():
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=2) as resp:
            json.loads(resp.read())
        return True
    except Exception:
        return False


def _engine_start():
    binary = _engine_binary()
    if not binary:
        raise RuntimeError("no engine binary found in Shared/bin")
    if os.path.basename(binary) == "llama-server-android":
        raise RuntimeError("Android engine needs its model args; use Android/start.sh")
    base = os.path.dirname(SCRIPT_DIR)
    runtime = os.path.abspath(os.path.join(base, "models", ".ollama-runtime"))
    os.makedirs(os.path.join(runtime, "tmp"), exist_ok=True)
    env = dict(os.environ)
    env.update({
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_ORIGINS": "*",
        "OLLAMA_HOME": runtime,
        "OLLAMA_TMPDIR": os.path.join(runtime, "tmp"),
        "OLLAMA_MODELS": os.path.join(base, "models", "ollama_data"),
        "HOME": runtime,
    })
    flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS on Windows
    subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=runtime, creationflags=flags)
    return {"started": True, "binary": binary, "runtime": runtime}


def _engine_stop():
    names = {"ollama-linux", "ollama-darwin", "ollama-windows.exe", "llama-server-android"}
    killed = []
    if HAS_PSUTIL:
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info.get("name") or "").lower() in names:
                try:
                    p.terminate()
                    killed.append(p.info.get("pid"))
                except Exception:
                    pass
    else:
        subprocess.run(["pkill", "-f", "ollama-linux|ollama-darwin|llama-server-android"],
                       capture_output=True)
    return {"stopped": True, "pids": killed}


def _power_action(action):
    action = str(action or "").lower()
    if action not in ("shutdown", "reboot", "sleep"):
        raise ValueError("action must be shutdown, reboot or sleep")
    system = platform.system()
    if system == "Windows":
        cmd = {"shutdown": ["shutdown", "/s", "/t", "5"],
               "reboot": ["shutdown", "/r", "/t", "5"],
               "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]}[action]
    elif system == "Darwin":
        cmd = {"shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
               "reboot": ["osascript", "-e", 'tell app "System Events" to restart'],
               "sleep": ["pmset", "sleepnow"]}[action]
    else:
        cmd = {"shutdown": ["systemctl", "poweroff"],
               "reboot": ["systemctl", "reboot"],
               "sleep": ["systemctl", "suspend"]}[action]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"action": action, "scheduled": True}


def _server_restart():
    """Re-exec this server in place after the current response is flushed."""
    script = os.path.abspath(__file__)
    argv = [sys.executable, script] + sys.argv[1:]

    def _do():
        time.sleep(1.0)
        os.execv(sys.executable, argv)

    threading.Thread(target=_do, daemon=True).start()
    return {"restarting": True, "pid": os.getpid()}


def _code_fingerprint():
    """SHA-256 over the core source files, so Hermes can detect tampering."""
    h = hashlib.sha256()
    for rel in ("Shared/chat_server.py", "Shared/analytics.py", "Shared/maintenance.py",
                "Shared/scripts/zentha_mini_ctl.py", "Linux/install.sh",
                "Mac/install.command", "Windows/install-core.ps1"):
        path = os.path.join(APP_ROOT, rel)
        h.update(rel.encode())
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b":missing")
    return h.hexdigest()


def _monitor_snapshot():
    """Rich live snapshot for Hermes monitoring."""
    cpu, ram = _get_hw_stats()
    disk = shutil.disk_usage(APP_ROOT)
    try:
        models = [m.get("name") for m in json.loads(
            urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=3).read()).get("models", [])]
    except Exception:
        models = None
    return {
        **_install_fingerprint(),
        "version": _read_version(),
        "uptime_seconds": int(time.time() - SERVER_START_TIME),
        "cpu_percent": cpu,
        "ram_percent": ram,
        "disk": {"total_gb": round(disk.total / 1024 ** 3, 2),
                 "free_gb": round(disk.free / 1024 ** 3, 2)},
        "engine_up": _engine_is_up(),
        "models": models,
        "engine_binary": os.path.basename(_engine_binary() or ""),
        "code_sha256": _code_fingerprint(),
        "lan_mode": LAN_MODE,
        "hermes_enabled": bool((_load_settings_file().get("hermes") or {}).get("enabled")),
    }


def _push_hermes_event(event, payload):
    """Best-effort outbound notification to the Hermes webhook.

    HTTPS is always allowed. Plain HTTP is allowed only for loopback/private
    hosts so a local Hermes install still works, while public endpoints must
    use TLS (anti-MITM). Every push carries the install ID and is HMAC-signed
    when a secret is configured.
    """
    try:
        h = (_load_settings_file().get("hermes") or {})
        if not h.get("enabled"):
            return
        url = (h.get("webhook_url") or "").strip()
        if not url:
            return
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme == "http":
            if not _is_private_host(parsed.hostname or ""):
                return
        elif scheme != "https":
            return
        install_id = _get_or_create_install_id()
        envelope = {"event": event, "install_id": install_id,
                    "sent_at": datetime.now().astimezone().isoformat(),
                    "payload": payload}
        if event == "command" and isinstance(payload, dict):
            command = payload.get("command")
            if command in PUSH_REDACTED_COMMANDS:
                envelope["payload"] = {"command": command, "ok": payload.get("ok"),
                                       "redacted": True}
        data = json.dumps(envelope).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "X-Zentha-Install-Id": install_id})
        secret = h.get("secret")
        if secret:
            req.add_header("X-Zentha-Signature",
                           hmac.new(secret.encode(), data, hashlib.sha256).hexdigest())
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _hermes_heartbeat_loop():
    """Report liveness + metrics to Hermes so it can monitor without polling."""
    last = 0.0
    while True:
        try:
            h = (_load_settings_file().get("hermes") or {})
            interval = int(h.get("heartbeat_seconds") or 60)
        except Exception:
            h, interval = {}, 60
        interval = max(15, min(interval or 60, 3600))
        now = time.time()
        if now - last >= interval:
            try:
                if h.get("enabled"):
                    _push_hermes_event("heartbeat", _monitor_snapshot())
            except Exception:
                pass
            last = now
        time.sleep(min(10, interval))


def _device_info():
    info = {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "hostname": _run(["hostname"]),
        "python": platform.python_version(),
        "cpu_logical": os.cpu_count(),
    }
    info["cpu_percent"], info["ram_percent"] = _get_hw_stats()
    info["uname"] = _run(["uname", "-a"])
    info["uptime"] = _run(["uptime", "-p"]) or _run(["cat", "/proc/uptime"])
    info["user"] = os.environ.get("USER") or _run(["whoami"])
    disk = _run(["df", "-h", "/"])
    info["disk"] = disk.splitlines()[-1] if disk else None
    return info


def _network_info():
    ipv4, ipv6 = [], []
    for line in _run(["ip", "-o", "addr", "show"]).splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] in ("inet", "inet6"):
            (ipv4 if parts[2] == "inet" else ipv6).append(parts[3])
    dns = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as f:
            dns = [ln.split()[1] for ln in f if ln.startswith("nameserver") and len(ln.split()) > 1]
    except Exception:
        pass
    return {
        "ipv4": ipv4,
        "ipv6": ipv6,
        "dns": dns,
        "gateway": _run(["ip", "route", "show", "default"]),
        "interfaces": _run(["ip", "-brief", "addr"]),
        "public_ip": None,
    }


def _listening_ports():
    out = []
    text = _run(["ss", "-ltnp"])
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            out.append({"proto": "tcp", "state": parts[0], "address": parts[3],
                        "process": " ".join(parts[5:]) if len(parts) > 5 else ""})
    text6 = _run(["ss", "-lunp"])
    for line in text6.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            out.append({"proto": "udp", "state": parts[0], "address": parts[3],
                        "process": " ".join(parts[5:]) if len(parts) > 5 else ""})
    return out


def _browser_data():
    """Aggregated browser intelligence: counts and top domains, never raw cookie values."""
    home = os.path.expanduser("~")
    candidates = [
        ("chrome", os.path.join(home, ".config/google-chrome/Default")),
        ("chromium", os.path.join(home, ".config/chromium/Default")),
        ("firefox", None),
    ]
    result = {"profiles": [], "history": [], "cookies": [], "cache_mb": 0.0}
    import glob
    for name, path in candidates:
        if name == "firefox":
            profiles = glob.glob(os.path.join(home, ".mozilla/firefox/*default*"))
        else:
            profiles = [path] if path and os.path.isdir(path) else []
        for profile in profiles:
            entry = {"browser": name, "profile": os.path.basename(profile)}
            history_db = os.path.join(profile, "History")
            if os.path.exists(history_db):
                try:
                    import sqlite3
                    from urllib.parse import urlparse
                    con = sqlite3.connect("file:%s?mode=ro" % history_db, uri=True)
                    rows = con.execute(
                        "SELECT url, visit_count, last_visit_time FROM urls "
                        "ORDER BY visit_count DESC LIMIT 500").fetchall()
                    hosts = {}
                    for url, visits, _ts in rows:
                        host = urlparse(url).netloc or "(direct)"
                        hosts[host] = hosts.get(host, 0) + (visits or 0)
                    top = sorted(hosts.items(), key=lambda kv: kv[1], reverse=True)[:10]
                    entry["history_top"] = [{"host": h, "visits": c} for h, c in top]
                    entry["history_total"] = con.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
                    con.close()
                except Exception as exc:
                    entry["history_error"] = str(exc)[:120]
            cookies_db = os.path.join(profile, "Cookies")
            if not os.path.exists(cookies_db):
                cookies_db = os.path.join(profile, "cookies.sqlite")
            if os.path.exists(cookies_db):
                try:
                    import sqlite3
                    con = sqlite3.connect("file:%s?mode=ro" % cookies_db, uri=True)
                    # Fixed, literal SQL: no user/agent input is ever interpolated here.
                    rows = con.execute(
                        "SELECT host_key, COUNT(*) FROM cookies ORDER BY 2 DESC LIMIT 10"
                    ).fetchall()
                    entry["cookie_origins"] = [{"host": h, "count": c} for h, c in rows]
                    entry["cookie_total"] = con.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
                    con.close()
                except Exception as exc:
                    entry["cookie_error"] = str(exc)[:120]
            if entry.get("history_top") or entry.get("cookie_origins"):
                result["profiles"].append(entry)
    cache_dirs = [
        os.path.join(home, ".cache/google-chrome"),
        os.path.join(home, ".cache/chromium"),
        os.path.join(home, ".cache/mozilla/firefox"),
    ]
    total = 0
    for d in cache_dirs:
        if os.path.isdir(d):
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    try:
                        total += os.path.getsize(os.path.join(root, fn))
                    except OSError:
                        pass
    result["cache_mb"] = round(total / (1024 * 1024), 1)
    return result


def _camera_snap():
    _ensure_voice_dir()
    out = os.path.join(VOICE_DIR, "camera_%d.jpg" % int(time.time() * 1000))
    ok = subprocess.run(["ffmpeg", "-y", "-f", "v4l2", "-video_size", "640x480",
                         "-i", "/dev/video0", "-frames:v", "1", out],
                        capture_output=True, timeout=20).returncode == 0
    return {"ok": ok, "path": out if ok else None, "device": "/dev/video0"}


def _mic_record(seconds=5):
    _ensure_voice_dir()
    out = os.path.join(VOICE_DIR, "mic_%d.ogg" % int(time.time() * 1000))
    ok = subprocess.run(["ffmpeg", "-y", "-f", "alsa", "-i", "default", "-t", str(seconds),
                         "-ar", "16000", "-ac", "1", out],
                        capture_output=True, timeout=max(10, seconds + 10)).returncode == 0
    return {"ok": ok, "path": out if ok else None, "seconds": seconds}


def _location_info():
    """Local location sources only: no external geolocation call."""
    out = {"geo": None, "source": None}
    geo = _run(["geoclue-cli", "--format", "json"], timeout=5)
    if geo:
        out["geo"], out["source"] = geo, "geoclue"
    return out


def _telemetry_raw():
    try:
        with open(os.path.join(CHATS_DIR, "telemetry.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _intel_report():
    """Fuse every local source into one device-intelligence view (Palantir-style)."""
    device = _device_info()
    network = _network_info()
    ports = _listening_ports()
    browser = _browser_data()
    chats = _load_chats_file()
    observations = []

    exposed = [p for p in ports if not p["address"].startswith(("127.", "[::1]", "::1"))
               and not p["address"].split(":")[0] in ("127.0.0.1", "::1")]
    loopback = [p for p in ports if p not in exposed]
    observations.append("listening: %d total, %d loopback-only, %d reachable off-box"
                        % (len(ports), len(loopback), len(exposed)))
    for p in exposed[:8]:
        observations.append("exposed %s %s %s" % (p["proto"], p["address"], p["process"]))
    observations.append("network: %d IPv4, %d IPv6, DNS %s"
                        % (len(network["ipv4"]), len(network["ipv6"]), ",".join(network["dns"]) or "-"))
    observations.append("browser: %d profiles, %.1f MB cache"
                        % (len(browser["profiles"]), browser["cache_mb"]))
    observations.append("chats: %d stored conversations" % len(chats))
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "device": device,
        "network": network,
        "listening_ports": ports,
        "telemetry": _telemetry_raw(),
        "browser": browser,
        "chats_summary": [{"id": c.get("id"), "title": c.get("title"),
                           "messages": len(c.get("msgs") or [])} for c in chats if isinstance(c, dict)],
        "observations": observations,
    }


class LogFormatter(logging.Formatter):
    """Readable, multi-line formatter with strict spacing and rich context."""

    def format(self, record):
        local_now = datetime.now().astimezone()
        timestamp = local_now.isoformat()
        tz_label = local_now.tzname() or "local"
        exception_text = ""
        if record.exc_info:
            exception_text = self.formatException(record.exc_info)
        hardware = getattr(record, "hardware_specs", None) or {}
        request_headers = getattr(record, "request_headers", None) or {}
        lines = [
            "=" * 96,
            f"timestamp     : {timestamp}",
            f"timezone      : {tz_label}",
            f"level         : {record.levelname}",
            f"event         : {record.getMessage()}",
            "",
            "request_context",
            f"  request_id   : {getattr(record, 'request_id', '-')}",
            f"  method       : {getattr(record, 'http_method', '-')}",
            f"  path         : {getattr(record, 'path', '-')}",
            f"  api_source   : {getattr(record, 'api_source', '-')}",
            f"  client_ip    : {getattr(record, 'client_ip', '-')}",
            f"  user_agent   : {request_headers.get('User-Agent', '-')}",
            f"  model_name   : {getattr(record, 'model_name', '-')}",
            f"  model_temp   : {getattr(record, 'model_temperature', '-')}",
            f"  model_stream : {getattr(record, 'model_stream', '-')}",
            "",
            "code_context",
            f"  logger       : {record.name}",
            f"  module       : {record.module}",
            f"  function     : {record.funcName}",
            f"  line         : {record.lineno}",
            f"  thread       : {record.threadName}",
            "",
            "hardware_specs",
            f"  platform     : {hardware.get('platform', '-')}",
            f"  release      : {hardware.get('platform_release', '-')}",
            f"  machine      : {hardware.get('machine', '-')}",
            f"  processor    : {hardware.get('processor', '-')}",
            f"  cpu_logical  : {hardware.get('cpu_count_logical', '-')}",
            f"  ram_total_gb : {hardware.get('ram_total_gb', '-')}",
            f"  python       : {hardware.get('python_version', '-')}",
        ]
        if exception_text:
            lines.extend(["", "exception", exception_text])
        lines.append("=" * 96)
        return "\n".join(lines)

class ImmediateFlushRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Flush each record immediately so logs are visible without waiting."""

    def emit(self, record):
        super().emit(record)
        try:
            self.flush()
        except Exception:
            pass

def configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("chat_server")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    logger.propagate = False

    log_queue = queue.Queue(maxsize=10000)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    logger.addHandler(queue_handler)

    file_handler = ImmediateFlushRotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=1, encoding="utf-8"
    )
    file_handler.setFormatter(LogFormatter())

    listener = logging.handlers.QueueListener(log_queue, file_handler, respect_handler_level=True)
    listener.start()
    return logger, listener

def _log_event(level, message, request_context=None, exc_info=False):
    if not _is_log_enabled(level):
        return
    request_context = request_context or {}
    LOGGER.log(
        level,
        message,
        extra={
            "request_id": request_context.get("request_id", "-"),
            "http_method": request_context.get("method", "-"),
            "path": request_context.get("path", "-"),
            "api_source": request_context.get("api_source", "-"),
            "client_ip": request_context.get("client_ip", "-"),
            "request_headers": request_context.get("request_headers", {}),
            "model_name": request_context.get("model_name", "-"),
            "model_temperature": request_context.get("model_temperature", "-"),
            "model_stream": request_context.get("model_stream", "-"),
            "hardware_specs": HOST_HARDWARE_SPECS,
        },
        exc_info=exc_info,
    )

def ensure_data_dir():
    """Create required data folders if they don't exist."""
    os.makedirs(CHATS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    _ensure_password()
    if not os.path.exists(CHATS_FILE):
        with open(CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "globalSystemPrompt": _load_soul_text(),
                    "temperature": 0.7,
                    "logMode": DEFAULT_LOG_MODE,
                    "memory": {"enabled": True, "items": []},
                    "skills": {"enabled": True, "items": []},
                    "tools": {"enabled": True, "items": []},
                    "plugins": {"enabled": True, "items": []},
                    "connectors": {"enabled": True, "items": []},
                    "mcp": {"enabled": True, "servers": []},
                    "supportUrl": "https://saweria.co/ZenthralixLab",
                    "websiteUrl": "https://zenthralix.vercel.app",
                    "hermes": {"enabled": False, "webhook_url": "", "secret": "", "agent_token": ""},
                    "auth_password_hash": "",
                    "telemetry": False,
                    "improveModelForEveryone": False,
                },
                f,
            )

class ChatHandler(http.server.BaseHTTPRequestHandler):
    """Handles all HTTP requests for the ZenthaMini Chat."""

    def _build_request_context(self, api_source):
        headers = {}
        for key in ("User-Agent", "Authorization"):
            value = self.headers.get(key)
            if value:
                headers[key] = value
        return {
            "request_id": str(uuid.uuid4()),
            "method": getattr(self, "command", "-"),
            "path": getattr(self, "path", "-"),
            "api_source": api_source,
            "client_ip": self.client_address[0] if self.client_address else "-",
            "request_headers": headers,
        }

    def _read_body(self):
        """Read once and cache, so HMAC verification and handlers share one body."""
        cached = getattr(self, "_body_cache", None)
        if cached is not None:
            return cached
        length = max(0, _safe_int(self.headers.get("Content-Length"), 0))
        self._body_cache = self.rfile.read(length) if length > 0 else b""
        return self._body_cache

    def _agent_authorized(self):
        """Token + HMAC gate with brute-force lockout and optional mandatory TLS."""
        self._agent_hmac_ok = False
        client_ip = self.client_address[0] if self.client_address else "unknown"
        if _agent_throttled(client_ip):
            return False
        ok = self._agent_authorized_inner(client_ip)
        if ok:
            _agent_succeeded(client_ip)
        else:
            _agent_failed(client_ip)
        return ok

    def _agent_authorized_inner(self, client_ip):
        # Header only: query tokens leak into logs, browser history and Referer.
        token = self.headers.get("X-Agent-Token") or ""
        if not token or not hmac.compare_digest(token, _effective_agent_token()):
            return False
        settings = _load_settings_file()
        hermes = settings.get("hermes") or {}
        secret = hermes.get("secret") or ""
        # Anti-MITM: when require_https is on, non-loopback agent traffic must be TLS.
        if hermes.get("require_https") and not _is_loopback_addr(client_ip):
            if not isinstance(self.connection, ssl.SSLSocket):
                return False
        if not secret:
            return True
        ts = self.headers.get("X-Agent-Timestamp") or ""
        nonce = self.headers.get("X-Agent-Nonce") or ""
        sig = self.headers.get("X-Agent-Signature") or ""
        if not (ts and nonce and sig):
            return False
        try:
            if abs(time.time() - float(ts)) > AGENT_NONCE_WINDOW:
                return False
        except (TypeError, ValueError):
            return False
        # Body is included so a captured signed request cannot be replayed with
        # a different command/payload inside the time window.
        body_hash = hashlib.sha256(self._read_body()).hexdigest()
        msg = f"{self.command}:{urlparse(self.path).path}:{ts}:{nonce}:{body_hash}".encode()
        calc = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, sig):
            return False
        # This request is cryptographically attributable to the holder of the
        # Hermes secret, so it may use the full-control commands.
        self._agent_hmac_ok = True
        now = time.time()
        with AGENT_NONCES_LOCK:
            for key, seen in list(AGENT_NONCES.items()):
                if now - seen > AGENT_NONCE_WINDOW:
                    AGENT_NONCES.pop(key, None)
            if nonce in AGENT_NONCES:
                return False
            AGENT_NONCES[nonce] = now
        return True

    def _send_agent_json(self, result):
        """Write a JSON response signed with HMAC-SHA256 for integrity (anti-MITM)."""
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Zentha-Install-Id", _get_or_create_install_id())
        secret = _agent_secret()
        if secret:
            self.send_header("X-Zentha-Response-Signature",
                             hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest())
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self):
        self.send_response(401)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "unauthorized"}).encode())

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return _is_private_host(host)

    def _is_authorized(self):
        if not LAN_MODE:
            return True
        token = None
        # Cookie
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("zm_token="):
                token = part[len("zm_token="):]
        # Header
        if not token:
            token = self.headers.get("X-Zentha-Token")
        # Query param
        if not token:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            token = (qs.get("token") or [None])[0]
        return token == AUTH_TOKEN

    def _set_session_cookie(self, sid):
        self.send_header("Set-Cookie", f"zm_session={sid}; Path=/; HttpOnly; SameSite=Strict")

    def _get_session(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("zm_session="):
                return part[len("zm_session="):]
        return None

    def _is_session_valid(self):
        sid = self._get_session()
        if not sid:
            return False
        with _SESSIONS_LOCK:
            if len(_SESSIONS) > 200:
                for key, expiry in list(_SESSIONS.items()):
                    if time.time() > expiry:
                        _SESSIONS.pop(key, None)
            exp = _SESSIONS.get(sid)
            if not exp:
                return False
            if time.time() > exp:
                _SESSIONS.pop(sid, None)
                return False
            return True

    def _require_session_if_remote(self):
        # loopback is trusted; remote requires login
        if self.client_address[0] in ("127.0.0.1", "::1"):
            return True
        return self._is_session_valid()
    def _set_auth_cookie(self):
        if LAN_MODE and AUTH_TOKEN:
            self.send_header("Set-Cookie", f"zm_token={AUTH_TOKEN}; Path=/; HttpOnly; SameSite=Strict")

    def log_message(self, format, *args):
        """Print all requests for easy debugging."""
        msg = format % args
        msg = re.sub(r"([?&](?:token|agent_token)=)[^&\s]+", r"\1<redacted>", msg)
        ts = time.strftime("%H:%M:%S")
        # Colour-code by status: errors red, warnings yellow, ok green
        if "404" in msg or "500" in msg or "502" in msg:
            prefix = "  \033[91m[ERR]\033[0m"
        elif "200" in msg or "204" in msg:
            prefix = "  \033[92m[ OK]\033[0m"
        else:
            prefix = "  \033[93m[---]\033[0m"
        print(f"{prefix} {ts}  {msg}")

    # ── CORS headers ───────────────────────────────────────────
    def end_headers(self):
        # Security headers (anti-MITM / hardening)
        try:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(self)")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; connect-src 'self' http://127.0.0.1:* http://localhost:*; img-src 'self' data: blob:; font-src 'self' data:")
        except Exception:
            pass
        super().end_headers()

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        # Only allow same-origin / localhost / private LAN origins
        allowed = False
        if origin:
            from urllib.parse import urlparse as _u
            host = _u(origin).hostname or ""
            if _is_private_host(host):
                allowed = True
        if allowed and origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Routing ────────────────────────────────────────────────
    def do_GET(self):
        if not _rate_limit_ok(self.client_address[0] if self.client_address else 'unknown'):
            self.send_response(429)
            self.end_headers()
            return
        path = urlparse(self.path).path
        is_agent_path = path.startswith("/api/agent/")
        # Agent/API endpoints authenticate with the agent token (and optional HMAC),
        # not with the browser Host header or login session. That is what lets Hermes
        # on another machine monitor and control this install.
        if not is_agent_path and not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return

        # Auth gate for API/ollama
        if path.startswith("/api/") or path.startswith("/ollama/"):
            if (not is_agent_path and path not in ("/api/login", "/api/logout")
                    and not self._require_session_if_remote()):
                self.send_response(401)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "login required"}).encode())
                return
            if not is_agent_path and not self._is_authorized():
                self.send_response(401)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                return

        # Login page
        if path == "/login":
            self._serve_login()
            return

        # Serve the main UI
        if path == "/" or path == "/index.html":
            # If token provided in URL, set cookie
            from urllib.parse import parse_qs, urlparse as _u
            qs = parse_qs(_u(self.path).query)
            if qs.get("token") and qs.get("token")[0] == AUTH_TOKEN:
                self._serve_html_with_cookie()
                return
            if not self._require_session_if_remote():
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            self._serve_html()

        # Telemetry API
        elif path == "/api/telemetry":
            self._get_telemetry()

        # Chat data API
        elif path == "/api/chats":
            self._get_chats()

        # Settings API
        elif path == "/api/settings":
            self._get_settings()

        # Version API
        elif path == "/api/version":
            self._get_version()

        # Update check API
        elif path == "/api/update/check":
            self._check_update()

        # Agent API (Hermes integration)
        elif path == "/api/agent/status":
            if not self._agent_authorized():
                self._unauthorized(); return
            self._agent_status()
        elif path == "/api/agent/logs":
            if not self._agent_authorized():
                self._unauthorized(); return
            self._agent_logs()
        elif path == "/api/agent/settings":
            if not self._agent_authorized():
                self._unauthorized(); return
            self._agent_get_settings()

        elif path == "/api/agent/token":
            if self.client_address[0] not in ("127.0.0.1", "::1") and not self._agent_authorized():
                self._unauthorized(); return
            self._agent_token()

        # Hardware stats API
        elif path == "/api/stats":
            self._get_stats()

        # GitHub plugin fetch API
        elif path == "/api/github/plugin":
            self._get_github_plugin()

        # Proxy Ollama API
        elif path.startswith("/ollama/"):
            self._proxy_ollama("GET")

        else:
            # Try serving static files from SCRIPT_DIR
            self._serve_static(path)

    def do_POST(self):
        if not _rate_limit_ok(self.client_address[0] if self.client_address else 'unknown'):
            self.send_response(429)
            self.end_headers()
            return
        path = urlparse(self.path).path
        is_agent_path = path.startswith("/api/agent/")
        if not is_agent_path and not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return
        limit = BODY_LIMITS.get(path, MAX_BODY_BYTES)
        if max(0, _safe_int(self.headers.get("Content-Length"), 0)) > limit:
            self.send_response(413)
            self._cors_headers()
            self.end_headers()
            return

        if path.startswith("/api/") or path.startswith("/ollama/"):
            if (not is_agent_path and path not in ("/api/version", "/api/update/check", "/api/agent/token")
                    and not self._require_session_if_remote()):
                self.send_response(401)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "login required"}).encode())
                return
            if not is_agent_path and not self._is_authorized():
                self.send_response(401)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                return

        if path == "/api/login":
            self._api_login()
            return
        elif path == "/api/logout":
            self._api_logout()
            return

        if path == "/api/telemetry":
            self._save_telemetry()
            return
        if path == "/api/chats":
            self._save_chats()

        elif path == "/api/settings":
            self._save_settings()

        elif path == "/api/agent/command":
            if not self._agent_authorized():
                self._unauthorized(); return
            self._agent_command()

        # Proxy Ollama API
        elif path.startswith("/ollama/"):
            self._proxy_ollama("POST")

        else:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()

    def do_DELETE(self):
        if not _rate_limit_ok(self.client_address[0] if self.client_address else 'unknown'):
            self.send_response(429)
            self.end_headers()
            return
        if not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return
        path = urlparse(self.path).path
        if path.startswith("/api/") or path.startswith("/ollama/"):
            if not self._is_authorized():
                self.send_response(401)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                return
        if path.startswith("/ollama/"):
            self._proxy_ollama("DELETE")
        else:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()

    # ── Serve HTML ─────────────────────────────────────────────
    def _serve_login(self):
        html = """<!doctype html><html><head><meta charset='utf-8'><title>Login - ZenthaMini</title>
        <style>body{font-family:sans-serif;background:#1e1e2e;color:#eee;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
        .card{background:#2a2a3e;padding:30px;border-radius:12px;min-width:300px}input{width:100%;padding:10px;margin:8px 0;border-radius:6px;border:1px solid #444;background:#1e1e2e;color:#eee}button{width:100%;padding:10px;border:none;border-radius:6px;background:#7c5cff;color:#fff;cursor:pointer}</style></head>
        <body><div class='card'><h2>ZenthaMini Login</h2><input id='pw' type='password' placeholder='Password'/><button onclick='login()'>Login</button><p id='msg' style='color:#f66'></p></div>
        <script>async function login(){const pw=document.getElementById('pw').value;const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});const d=await r.json();if(r.ok){location.href='/'}else{document.getElementById('msg').textContent=d.error||'login failed'}}</script></body></html>"""
        content = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(content)

    def _api_login(self):
        client_ip = self.client_address[0] if self.client_address else "unknown"
        if _login_throttled(client_ip):
            self.send_response(429)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "too many attempts, try again later"}).encode())
            return
        _ensure_password()
        body = self._read_body()
        try:
            incoming = json.loads(body) if body else {}
        except Exception:
            incoming = {}
        pw = incoming.get("password", "")
        if not _verify_password(pw, _AUTH_HASH):
            _login_failed(client_ip)
            self.send_response(401)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "invalid password"}).encode())
            return
        _login_succeeded(client_ip)
        sid = uuid.uuid4().hex
        with _SESSIONS_LOCK:
            _SESSIONS[sid] = time.time() + 86400
        # push login event to Hermes
        try:
            self._push_hermes_event("login", {"client_ip": self.client_address[0]})
        except Exception:
            pass
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self._set_session_cookie(sid)
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def _api_logout(self):
        sid = self._get_session()
        if sid:
            with _SESSIONS_LOCK:
                _SESSIONS.pop(sid, None)
        self.send_response(200)
        self.send_header("Set-Cookie", "zm_session=; Path=/; Max-Age=0")
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def _serve_html(self):
        try:
            with open(HTML_FILE, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"ZenthaMini.html not found.")

    def _serve_html_with_cookie(self):
        self._set_auth_cookie()
        self._serve_html()
    def _serve_static(self, path):
        """Serve static files (CSS, JS, images) from SCRIPT_DIR."""
        safe_path = os.path.normpath(path.lstrip("/"))
        # Block sensitive/runtime/private paths
        blocked = ("chat_data", "logs", ".ollama-runtime", "models", "bin", "lib")
        if safe_path.split(os.sep)[0] in blocked or safe_path.startswith("."):
            self.send_response(403)
            self.end_headers()
            return
        full_path = os.path.join(SCRIPT_DIR, safe_path)

        # Security: don't allow path traversal
        if not os.path.abspath(full_path).startswith(os.path.abspath(SCRIPT_DIR)):
            self.send_response(403)
            self.end_headers()
            return

        # Only serve known-safe file types
        ext = os.path.splitext(full_path)[1].lower()
        if ext not in (".html", ".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2", ".mjs"):
            self.send_response(403)
            self.end_headers()
            return

        if os.path.isfile(full_path):
            ext = os.path.splitext(full_path)[1].lower()
            mime_types = {
                ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
                ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
                ".svg": "image/svg+xml", ".ico": "image/x-icon"
            }
            content_type = mime_types.get(ext, "application/octet-stream")
            with open(full_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()

    # ── Chat Persistence ───────────────────────────────────────
    def _get_chats(self):
        request_context = self._build_request_context("/api/chats")
        try:
            with DATA_FILE_LOCK:
                with open(CHATS_FILE, "r", encoding="utf-8") as f:
                    data = f.read()
        except (FileNotFoundError, json.JSONDecodeError):
            data = "[]"
        except Exception:
            _log_event(logging.ERROR, "Failed to read chats file", request_context=request_context, exc_info=True)
            data = "[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def _save_chats(self):
        request_context = self._build_request_context("/api/chats")
        # Limit payload size (10 MB)
        cl = _safe_int(self.headers.get("Content-Length"), 0)
        if cl > 10 * 1024 * 1024:
            self.send_response(413)
            self._cors_headers()
            self.end_headers()
            return
        body = self._read_body()
        try:
            chats = json.loads(body)
            with DATA_FILE_LOCK:
                temp_file = CHATS_FILE + ".tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(chats, f, ensure_ascii=False, indent=2)
                    f.flush()
                os.replace(temp_file, CHATS_FILE)
                _chmod_secure(CHATS_FILE)
            _log_event(logging.INFO, "Chats saved successfully", request_context=request_context)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        except Exception as e:
            _log_event(logging.ERROR, "Failed to save chats", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "internal error"}).encode())

    def _get_settings(self):
        request_context = self._build_request_context("/api/settings")
        try:
            settings = _redact_settings(_load_settings_file())
            data = json.dumps(settings)
        except Exception:
            _log_event(logging.ERROR, "Failed to read settings file", request_context=request_context, exc_info=True)
            data = "{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def _save_settings(self):
        request_context = self._build_request_context("/api/settings")
        cl = _safe_int(self.headers.get("Content-Length"), 0)
        if cl > 1 * 1024 * 1024:
            self.send_response(413)
            self._cors_headers()
            self.end_headers()
            return
        body = self._read_body()
        try:
            incoming = json.loads(body)
            if not isinstance(incoming, dict):
                raise ValueError("Settings payload must be a JSON object")
            rejected = [k for k in incoming if k not in UI_SETTINGS_KEYS]
            if rejected:
                self.send_response(400)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "protected settings key",
                                             "keys": rejected}).encode())
                return

            with DATA_FILE_LOCK:
                settings = _load_settings_file()
                settings.update(incoming)
                settings["logMode"] = _normalize_log_mode(settings.get("logMode"))
                _persist_settings_file(settings)
            _set_active_log_mode(settings["logMode"])
            _log_event(logging.INFO, "Settings saved successfully", request_context=request_context)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "logMode": settings["logMode"]}).encode())
        except Exception as e:
            _log_event(logging.ERROR, "Failed to save settings", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "internal error"}).encode())

    # ── GitHub Plugin Fetch ─────────────────────────────────────
    def _get_github_plugin(self):
        """Fetch a plugin manifest (plugin.json) from a GitHub repo."""
        from urllib.parse import urlparse, parse_qs
        request_context = self._build_request_context("/api/github/plugin")
        qs = parse_qs(urlparse(self.path).query)
        repo = (qs.get("repo", [""])[0] or "").strip()
        path = (qs.get("path", ["plugin.json"])[0] or "plugin.json").strip()
        if not repo or "/" not in repo:
            self.send_response(400)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "repo must be owner/name"}).encode())
            return
        # sanitize
        if ".." in path or path.startswith("/"):
            path = "plugin.json"
        url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZenthaMini"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except Exception:
                data = {"raw": raw}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "repo": repo, "path": path, "data": data}).encode())
        except Exception as e:
            # try master branch
            try:
                url2 = f"https://raw.githubusercontent.com/{repo}/master/{path}"
                req2 = urllib.request.Request(url2, headers={"User-Agent": "ZenthaMini"})
                with urllib.request.urlopen(req2, timeout=15) as resp:
                    raw = resp.read().decode("utf-8")
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"raw": raw}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "repo": repo, "path": path, "data": data, "branch": "master"}).encode())
            except Exception as e2:
                _log_event(logging.ERROR, "GitHub plugin fetch failed", request_context=request_context, exc_info=True)
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e2)}).encode())

    def _get_version(self):
        request_context = self._build_request_context("/api/version")
        version = "1.0.0"
        for cand in (os.path.join(SCRIPT_DIR, "VERSION"), os.path.join(SCRIPT_DIR, "..", "VERSION")):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                if v:
                    version = v
                    break
            except Exception:
                continue
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps({"version": version}).encode())

    def _check_update(self):
        payload = _update_check_data()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    # ── Agent API (Hermes integration) ────────────────────────
    def _agent_token(self):
        self._send_agent_json({"ok": True, "token": _effective_agent_token(),
                               **_install_fingerprint()})

    def _agent_status(self):
        import socket
        settings = _load_settings_file()
        cpu, ram = _get_hw_stats()
        data = {
            "ok": True,
            "product": "ZenthaMini",
            **_install_fingerprint(),
            "version": _read_version(),
            "platform": platform.system(),
            "hostname": socket.gethostname(),
            "cpu_percent": cpu,
            "ram_percent": ram,
            "models": None,
            "lan_mode": LAN_MODE,
            "hermes": {
                "enabled": bool((settings.get("hermes") or {}).get("enabled")),
                "hmac_enabled": bool((settings.get("hermes") or {}).get("secret")),
                "require_https": bool((settings.get("hermes") or {}).get("require_https")),
            },
        }
        # Try Ollama models
        try:
            req = urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=5)
            tags = json.loads(req.read())
            data["models"] = [m.get("name") for m in tags.get("models", [])]
        except Exception:
            pass
        self._send_agent_json(data)

    def _agent_logs(self):
        # tail last 50 lines of log file
        lines = []
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            lines = all_lines[-50:]
        except Exception:
            pass
        self._send_agent_json({"ok": True, "lines": [l.rstrip("\n") for l in lines],
                               **_install_fingerprint()})

    def _agent_get_settings(self):
        s = _load_settings_file()
        # redact secrets
        if isinstance(s.get("hermes"), dict):
            s["hermes"].pop("secret", None)
            s["hermes"].pop("agent_token", None)
        self._send_agent_json(s)

    def _agent_command(self):
        body = self._read_body()
        try:
            incoming = json.loads(body) if body else {}
        except Exception:
            incoming = {}
        if not isinstance(incoming, dict):
            incoming = {}
        cmd = incoming.get("command")
        result = {"ok": False, "command": cmd}

        def _save_chats_list(chats):
            with DATA_FILE_LOCK:
                tmp = CHATS_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(chats, f, ensure_ascii=False, indent=2)
                    f.flush()
                os.replace(tmp, CHATS_FILE)
                _chmod_secure(CHATS_FILE)

        def _first_model():
            try:
                tags = json.loads(urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=5).read())
                names = [m.get("name") for m in tags.get("models", [])]
                if names:
                    return names[0]
            except Exception:
                pass
            return None

        def _resolve_model(explicit, chat, settings):
            return (explicit or (chat or {}).get("model") or settings.get("defaultModel") or _first_model())

        def _voice_file(value):
            if not isinstance(value, str) or not os.path.isabs(value):
                raise ValueError("path must be an existing file inside the voice directory")
            base = os.path.realpath(VOICE_DIR)
            resolved = os.path.realpath(value)
            if (os.path.commonpath([base, resolved]) != base
                    or not os.path.isfile(resolved)):
                raise ValueError("path must be an existing file inside the voice directory")
            return resolved

        try:
            if cmd == "clear_chats":
                _save_chats_list([])
                result = {"ok": True, "command": cmd}
            elif cmd == "reload_settings":
                _set_active_log_mode(_load_settings_file().get("logMode"))
                result = {"ok": True, "command": cmd}
            elif cmd == "check_update":
                result = {"ok": True, "command": cmd, **_update_check_data()}
            elif cmd == "maintenance_status":
                import socket
                telemetry_file = os.path.join(CHATS_DIR, "telemetry.json")
                settings = _load_settings_file()
                hermes = settings.get("hermes") or {}
                disk = shutil.disk_usage(APP_ROOT)
                cpu, ram = _get_hw_stats()
                update = zentha_maintenance.update_state(APP_ROOT, CHATS_DIR) if zentha_maintenance else {"error": "maintenance module unavailable"}
                result = {
                    "ok": True, "command": cmd, "product": "ZenthaMini",
                    **_install_fingerprint(),
                    "version": _read_version(), "platform": platform.system(),
                    "hostname": socket.gethostname(), "uptime_seconds": int(time.time() - SERVER_START_TIME),
                    "cpu_percent": cpu, "ram_percent": ram,
                    "disk": {"total_gb": round(disk.total / 1024**3, 2),
                             "free_gb": round(disk.free / 1024**3, 2)},
                    "lan_mode": LAN_MODE,
                    "telemetry": {
                        "consent": bool(settings.get("telemetry")),
                        "file_present": os.path.isfile(telemetry_file),
                        "age_seconds": (int(time.time() - os.path.getmtime(telemetry_file))
                                        if os.path.isfile(telemetry_file) else None),
                    },
                    "hermes": {"enabled": bool(hermes.get("enabled")),
                               "webhook_configured": bool(hermes.get("webhook_url")),
                               "hmac_enabled": bool(hermes.get("secret")),
                               "agent_token_configured": bool(hermes.get("agent_token"))},
                    "update": update,
                    "log_file": LOG_FILE,
                }
            elif cmd in ("update_check", "update_status", "update_prepare",
                         "update_apply", "update_rollback", "telemetry_clear"):
                if zentha_maintenance is None:
                    raise RuntimeError("maintenance module unavailable")
                if cmd in ("update_apply", "update_rollback", "telemetry_clear") and incoming.get("confirm") is not True:
                    raise ValueError("confirm: true is required for this command")
                if cmd == "update_check":
                    result = {"ok": True, "command": cmd, **_update_check_data()}
                elif cmd == "update_status":
                    result = {"ok": True, "command": cmd,
                              **zentha_maintenance.update_state(APP_ROOT, CHATS_DIR)}
                elif cmd == "update_prepare":
                    prepared = zentha_maintenance.stage(
                        APP_ROOT, CHATS_DIR, incoming.get("artifact"), incoming.get("version"))
                    result = {"ok": True, "command": cmd, **prepared}
                elif cmd == "update_apply":
                    applied = zentha_maintenance.apply_staged(
                        APP_ROOT, CHATS_DIR, incoming.get("version"))
                    result = {"ok": True, "command": cmd, **applied}
                elif cmd == "update_rollback":
                    result = {"ok": True, "command": cmd,
                              **zentha_maintenance.rollback(APP_ROOT, CHATS_DIR)}
                else:  # telemetry_clear
                    path = os.path.join(CHATS_DIR, "telemetry.json")
                    if os.path.exists(path):
                        os.unlink(path)
                    result = {"ok": True, "command": cmd, "deleted": True}
            elif cmd == "run_doctor":
                import socket
                cpu, ram = _get_hw_stats()
                doc = {
                    "ok": True,
                    "command": cmd,
                    **_install_fingerprint(),
                    "version": None,
                    "platform": platform.system(),
                    "hostname": socket.gethostname(),
                    "cpu_percent": cpu,
                    "ram_percent": ram,
                    "chat_data_exists": os.path.exists(CHATS_FILE),
                    "settings_exists": os.path.exists(SETTINGS_FILE),
                    "log_file": LOG_FILE,
                }
                doc["version"] = _read_version()
                result = doc
            elif cmd == "settings_get":
                result = {"ok": True, "command": cmd,
                          "settings": _redact_settings(_load_settings_file())}
            elif cmd == "settings_set":
                patch_data = incoming.get("settings") or {}
                if not isinstance(patch_data, dict):
                    raise ValueError("settings must be an object")
                if "auth_password_hash" in patch_data:
                    raise ValueError("auth_password_hash is protected")
                with DATA_FILE_LOCK:
                    s = _load_settings_file()
                    for key, value in patch_data.items():
                        if key == "hermes" and isinstance(value, dict) and isinstance(s.get("hermes"), dict):
                            s["hermes"].update(value)
                        else:
                            s[key] = value
                    s["logMode"] = _normalize_log_mode(s.get("logMode"))
                    _persist_settings_file(s)
                _set_active_log_mode(s["logMode"])
                result = {"ok": True, "command": cmd}
            elif cmd == "chats_list":
                chats = _load_chats_file()
                result = {"ok": True, "command": cmd, "chats": [
                    {"id": c.get("id"), "title": c.get("title"), "ts": c.get("ts"),
                     "model": c.get("model"), "messages": len(c.get("msgs") or [])}
                    for c in chats if isinstance(c, dict)
                ]}
            elif cmd == "chats_get":
                cid = incoming.get("id")
                chat = next((c for c in _load_chats_file()
                             if isinstance(c, dict) and c.get("id") == cid), None)
                result = {"ok": chat is not None, "command": cmd, "chat": chat}
            elif cmd == "chats_save":
                chats = incoming.get("chats")
                if not isinstance(chats, list):
                    raise ValueError("chats must be a list")
                _save_chats_list(chats)
                result = {"ok": True, "command": cmd, "count": len(chats)}
            elif cmd == "chat_delete":
                cid = incoming.get("id")
                _save_chats_list([c for c in _load_chats_file()
                                  if not (isinstance(c, dict) and c.get("id") == cid)])
                result = {"ok": True, "command": cmd}
            elif cmd == "models_list":
                result = {"ok": True, "command": cmd, "models": []}
                try:
                    tags = json.loads(urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=5).read())
                    result["models"] = [m.get("name") for m in tags.get("models", [])]
                except Exception as e:
                    result["error"] = str(e)
            elif cmd == "chat":
                prompt = str(incoming.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("prompt is required")
                settings = _load_settings_file()
                chat_id = incoming.get("chat_id")
                chats = _load_chats_file()
                chat = next((c for c in chats
                             if isinstance(c, dict) and c.get("id") == chat_id), None) if chat_id else None
                model = _resolve_model(incoming.get("model"), chat, settings)
                if not model:
                    raise RuntimeError("no model available")
                system = incoming.get("system")
                if system is None:
                    system = (chat or {}).get("sys") or settings.get("globalSystemPrompt") or ""
                messages = [{"role": "system", "content": system}] if system else []
                messages += [{"role": m.get("role", "user"), "content": m.get("content", "")}
                             for m in ((chat or {}).get("msgs") or []) if isinstance(m, dict)]
                messages.append({"role": "user", "content": prompt})
                reply, err = _ollama_chat(model, messages, temperature=incoming.get("temperature"))
                if err:
                    raise err
                if chat is not None:
                    # Reload under the lock: a long inference must not hold the
                    # lock, but a concurrent save must not lose this reply.
                    with DATA_FILE_LOCK:
                        fresh = _load_chats_file()
                        target = next((c for c in fresh
                                       if isinstance(c, dict) and c.get("id") == chat_id), None)
                        if target is None:
                            raise RuntimeError("chat was removed while generating")
                        msgs = target.setdefault("msgs", [])
                        # The UI may already have appended the user's message; never store it twice.
                        if not (msgs and msgs[-1].get("role") == "user"
                                and msgs[-1].get("content") == prompt):
                            msgs.append({"role": "user", "content": prompt})
                        msgs.append({"role": "assistant", "content": reply})
                        _save_chats_list(fresh)
                result = {"ok": True, "command": cmd, "model": model,
                          "reply": reply, "chat_id": (chat or {}).get("id")}
            elif cmd == "tts":
                text = str(incoming.get("text") or "").strip()
                if not text:
                    raise ValueError("text is required")
                path = _tts_ogg(text, incoming.get("lang") or "id")
                result = {"ok": True, "command": cmd, "path": path, "file": os.path.basename(path)}
            elif cmd == "stt":
                text, err = _stt_ogg(_voice_file(incoming.get("path")))
                if err:
                    raise err
                result = {"ok": True, "command": cmd, "text": text}
            elif cmd == "voice_chat":
                user_text, err = _stt_ogg(_voice_file(incoming.get("path")))
                if err:
                    raise err
                settings = _load_settings_file()
                model = _resolve_model(incoming.get("model"), None, settings)
                if not model:
                    raise RuntimeError("no model available")
                system = settings.get("globalSystemPrompt") or ""
                messages = [{"role": "system", "content": system}] if system else []
                messages.append({"role": "user", "content": user_text})
                reply, err = _ollama_chat(model, messages)
                if err:
                    raise err
                voice = _tts_ogg(reply, "id")
                result = {"ok": True, "command": cmd, "user_text": user_text, "reply": reply,
                          "reply_voice": voice, "reply_voice_file": os.path.basename(voice)}
            elif cmd in ("device_info", "network_info", "ports", "telemetry_get", "browser_data",
                         "camera_snap", "mic_record", "location_get", "intel", "analytics"):
                if cmd in PRIVILEGED_MONITOR_COMMANDS and not getattr(self, "_agent_hmac_ok", False):
                    raise PermissionError(
                        "%s requires HMAC signing (set hermes.secret and use the signed CLI)" % cmd)
                _intel_log(cmd, "sensitive" if cmd in SENSITIVE_COMMANDS else "",
                           self.client_address[0] if self.client_address else "unknown")
                if cmd == "device_info":
                    result = {"ok": True, "command": cmd, "device": _device_info()}
                elif cmd == "network_info":
                    result = {"ok": True, "command": cmd, "network": _network_info()}
                elif cmd == "ports":
                    result = {"ok": True, "command": cmd, "listening": _listening_ports()}
                elif cmd == "telemetry_get":
                    try:
                        with open(os.path.join(CHATS_DIR, "telemetry.json"), encoding="utf-8") as f:
                            tel = json.load(f)
                    except Exception:
                        tel = {}
                    result = {"ok": True, "command": cmd, "telemetry": tel}
                elif cmd == "browser_data":
                    result = {"ok": True, "command": cmd, "browser": _browser_data()}
                elif cmd == "camera_snap":
                    snap = _camera_snap()
                    snap["command"] = cmd
                    result = snap
                elif cmd == "mic_record":
                    try:
                        secs = int(incoming.get("seconds") or 5)
                    except Exception:
                        secs = 5
                    rec = _mic_record(max(1, min(60, secs)))
                    rec["command"] = cmd
                    result = rec
                elif cmd == "location_get":
                    result = {"ok": True, "command": cmd, "location": _location_info()}
                elif cmd == "analytics":
                    if zentha_analytics is None:
                        raise RuntimeError("analytics module unavailable")
                    report = zentha_analytics.analyze(_intel_report(), _load_chats_file())
                    result = {"ok": True, "command": cmd, "analytics": report}
                    if str(incoming.get("format") or "").lower() == "markdown":
                        result["markdown"] = zentha_analytics.render_markdown(report)
                else:
                    raw = _intel_report()
                    result = {"ok": True, "command": cmd, "report": raw}
                    if zentha_analytics is not None:
                        result["analytics"] = zentha_analytics.analyze(raw, _load_chats_file())
            elif cmd == "monitor":
                if not getattr(self, "_agent_hmac_ok", False):
                    raise PermissionError("monitor requires HMAC signing (set hermes.secret)")
                result = {"ok": True, "command": cmd, "monitor": _monitor_snapshot()}
            elif cmd == "install_id":
                result = {"ok": True, "command": cmd, **_install_fingerprint()}
            elif cmd in ("shell", "fs_list", "fs_read", "fs_write", "fs_mkdir",
                         "fs_delete", "fs_move", "process_list", "process_kill",
                         "engine_start", "engine_stop", "engine_status",
                         "server_restart", "power"):
                # Privilege boundary: only a request signed with the Hermes secret
                # (i.e. Zentha at Hermes) may drive the device. A bare token read
                # over localhost during setup is not enough for these commands.
                if not getattr(self, "_agent_hmac_ok", False):
                    raise PermissionError(
                        "full-access commands require HMAC signing: set hermes.secret "
                        "and use the signed CLI (setup --secret-file)")
                _intel_log(cmd, "control",
                           self.client_address[0] if self.client_address else "unknown")
                if cmd == "shell":
                    result = {"ok": True, "command": cmd,
                              **_run_shell(incoming.get("cmd"), incoming.get("timeout"),
                                           incoming.get("cwd"))}
                elif cmd == "fs_list":
                    result = {"ok": True, "command": cmd,
                              **_fs_list(incoming.get("path") or ".", incoming.get("limit") or 500)}
                elif cmd == "fs_read":
                    result = {"ok": True, "command": cmd,
                              **_fs_read(incoming.get("path"), incoming.get("max_bytes") or 200000)}
                elif cmd == "fs_write":
                    result = {"ok": True, "command": cmd,
                              **_fs_write(incoming.get("path"), incoming.get("content"),
                                          bool(incoming.get("append")))}
                elif cmd == "fs_mkdir":
                    target = _fs_path(incoming.get("path"))
                    os.makedirs(target, exist_ok=True)
                    result = {"ok": True, "command": cmd, "path": target}
                elif cmd == "fs_delete":
                    if incoming.get("confirm") is not True:
                        raise ValueError("confirm: true is required for fs_delete")
                    result = {"ok": True, "command": cmd, **_fs_delete(incoming.get("path"))}
                elif cmd == "fs_move":
                    result = {"ok": True, "command": cmd,
                              **_fs_move(incoming.get("from") or incoming.get("src"), incoming.get("to") or incoming.get("dst"))}
                elif cmd == "process_list":
                    result = {"ok": True, "command": cmd,
                              **_process_list(incoming.get("limit") or 200)}
                elif cmd == "process_kill":
                    if incoming.get("confirm") is not True:
                        raise ValueError("confirm: true is required for process_kill")
                    result = {"ok": True, "command": cmd,
                              **_process_kill(incoming.get("pid"), incoming.get("name"),
                                              bool(incoming.get("all")))}
                elif cmd == "engine_status":
                    result = {"ok": True, "command": cmd, "up": _engine_is_up(),
                              "binary": _engine_binary(), "host": OLLAMA_HOST}
                elif cmd == "engine_start":
                    result = {"ok": True, "command": cmd, **_engine_start()}
                elif cmd == "engine_stop":
                    result = {"ok": True, "command": cmd, **_engine_stop()}
                elif cmd == "server_restart":
                    if incoming.get("confirm") is not True:
                        raise ValueError("confirm: true is required for server_restart")
                    result = {"ok": True, "command": cmd, **_server_restart()}
                else:  # power
                    if incoming.get("confirm") is not True:
                        raise ValueError("confirm: true is required for power")
                    result = {"ok": True, "command": cmd,
                              **_power_action(incoming.get("action"))}
            else:
                result = {"ok": False, "command": cmd, "error": "unknown command"}
        except Exception as e:
            result = {"ok": False, "command": cmd, "error": str(e)}
        # Optional push to Hermes webhook if configured
        self._push_hermes_event("command", result)
        self._send_agent_json(result)

    def _push_hermes_event(self, event, payload):
        _push_hermes_event(event, payload)

    def _get_telemetry(self):
        if not self._require_session_if_remote() and self.client_address[0] not in ("127.0.0.1","::1"):
            self._unauthorized(); return
        f = os.path.join(CHATS_DIR, "telemetry.json")
        data = {}
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _save_telemetry(self):
        body = self._read_body()
        try:
            incoming = json.loads(body) if body else {}
        except Exception:
            incoming = {}
        # only store if consent
        if not incoming.get("consent"):
            # delete file
            f = os.path.join(CHATS_DIR, "telemetry.json")
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "stored": False}).encode())
            return
        # enrich with server-side client IP
        incoming.setdefault("data", {})
        incoming["data"]["client_ip"] = self.client_address[0] if self.client_address else None
        incoming["data"]["server_port"] = CHAT_SERVER_PORT
        incoming["timestamp"] = datetime.now().isoformat()
        f = os.path.join(CHATS_DIR, "telemetry.json")
        with DATA_FILE_LOCK:
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(incoming, fh, ensure_ascii=False, indent=2)
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "stored": True}).encode())

    # ── Hardware Stats ─────────────────────────────────────────
    def _get_stats(self):
        """Return CPU % and RAM % as JSON. Works with no external packages."""
        try:
            cpu, ram = _get_hw_stats()
            data = json.dumps({"cpu_percent": cpu, "ram_percent": ram, "has_psutil": HAS_PSUTIL})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(data.encode())
        except Exception as e:
            _log_event(
                logging.ERROR,
                "Failed to collect hardware stats",
                request_context=self._build_request_context("/api/stats"),
                exc_info=True,
            )
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "internal error"}).encode())

    # ── Ollama Proxy (streaming-aware) ─────────────────────────
    def _proxy_ollama(self, method):
        """
        Proxy requests from /ollama/* to the local Ollama engine.
        Supports streaming responses for /api/chat and /api/generate.
        """
        request_context = self._build_request_context("/ollama")
        # Strip the /ollama prefix to get the real Ollama path
        ollama_path = self.path[len("/ollama"):]
        target_url = OLLAMA_HOST + ollama_path

        # Read request body if present
        body = None
        content_length = max(0, _safe_int(self.headers.get("Content-Length"), 0))
        if content_length > 50 * 1024 * 1024:
            self.send_response(413)
            self._cors_headers()
            self.end_headers()
            return
        if content_length > 0:
            body = self.rfile.read(content_length)
        payload = {}
        if body:
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}

        if isinstance(payload, dict):
            options = payload.get("options")
            options = options if isinstance(options, dict) else {}
            request_context["model_name"] = payload.get("model", "-")
            request_context["model_stream"] = payload.get("stream", "-")
            request_context["model_temperature"] = options.get("temperature", "-")

        try:
            if method == "POST" and ollama_path == "/api/chat":
                model = payload.get("model") if isinstance(payload, dict) else None
                messages = payload.get("messages") if isinstance(payload, dict) else None

                if not isinstance(model, str) or not model.strip():
                    _log_event(
                        logging.ERROR,
                        "Rejected chat request with missing or empty model",
                        request_context=request_context,
                    )
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Missing or empty model field"}).encode())
                    return

                if not isinstance(messages, list) or not messages:
                    _log_event(
                        logging.ERROR,
                        "Rejected chat request with missing or empty messages",
                        request_context=request_context,
                    )
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Missing or empty messages field"}).encode())
                    return

            # Handle fake /api/tags for llama.cpp mode
            if LLAMA_CPP_MODE and ollama_path == "/api/tags":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"models":[{"name": "local-llama-model"}]}).encode())
                return

            if LLAMA_CPP_MODE and ollama_path == "/api/chat":
                # Translate Ollama payload -> OpenAI payload for llama-server
                ollama_req = json.loads(body) if body else {}
                ollama_req = ollama_req if isinstance(ollama_req, dict) else {}
                options = ollama_req.get("options")
                options = options if isinstance(options, dict) else {}
                openai_req = {
                    "messages": ollama_req.get("messages", []),
                    "stream": True,
                    "temperature": options.get("temperature", 0.7)
                }
                target_url = OLLAMA_HOST + "/v1/chat/completions"
                body = json.dumps(openai_req).encode()

            req = urllib.request.Request(
                target_url,
                data=body,
                method=method,
                headers={"Content-Type": self.headers.get("Content-Type", "application/json")}
            )

            # Optional: pass Authorization header if present
            if "Authorization" in self.headers:
                req.add_header("Authorization", self.headers.get("Authorization"))

            response = urllib.request.urlopen(req, timeout=600)

            # Send response headers
            self.send_response(response.status)
            is_stream = ("/api/chat" in ollama_path or "/api/generate" in ollama_path)

            for header, value in response.getheaders():
                lower = header.lower()
                if lower not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(header, value)

            self._cors_headers()
            self.end_headers()
            if method == "POST" and ("/api/chat" in ollama_path or "/api/generate" in ollama_path):
                _log_event(
                    logging.INFO,
                    f"Model request succeeded with upstream status {response.status}",
                    request_context=request_context,
                )

            # Stream the response in chunks
            sse_buffer = ""
            sse_started = False
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break

                # If bridging llama.cpp SSE to Ollama JSONL, keep partial lines
                # across reads: events are regularly split at 4096-byte edges.
                if LLAMA_CPP_MODE and is_stream:
                    sse_buffer += chunk.decode(errors="ignore")
                    lines = sse_buffer.split("\n")
                    sse_buffer = lines.pop()
                    finished = False
                    for line in lines:
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            finished = True
                            break
                        try:
                            event = json.loads(data)
                            choices = event.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta") or {}
                                out = {"message": {"role": "assistant",
                                                   "content": delta.get("content", "")},
                                       "done": False}
                                self.wfile.write((json.dumps(out) + "\n").encode())
                                self.wfile.flush()
                                sse_started = True
                        except (ValueError, TypeError, AttributeError):
                            continue
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            _log_event(logging.ERROR,
                                       "Client disconnected while streaming response",
                                       request_context=request_context, exc_info=True)
                            return
                    if finished:
                        break
                else:
                    try:
                        self.wfile.write(chunk)
                        if is_stream:
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        _log_event(
                            logging.ERROR,
                            "Client disconnected while streaming response",
                            request_context=request_context,
                            exc_info=True,
                        )
                        return
            if LLAMA_CPP_MODE and is_stream and sse_started:
                try:
                    self.wfile.write((json.dumps({"message": {"role": "assistant", "content": ""},
                                                  "done": True}) + "\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        except urllib.error.HTTPError as e:
            _log_event(
                logging.ERROR,
                f"Upstream HTTP error from Ollama: {e.code}",
                request_context=request_context,
                exc_info=True,
            )
            self.send_response(e.code)
            self._cors_headers()
            self.end_headers()
            try:
                self.wfile.write(e.read())
            except:
                pass

        except urllib.error.URLError as e:
            _log_event(
                logging.ERROR,
                "Failed to reach Ollama upstream",
                request_context=request_context,
                exc_info=True,
            )
            self.send_response(502)
            self._cors_headers()
            self.end_headers()
            msg = json.dumps({"error": f"Cannot reach Ollama engine: {str(e.reason)}"})
            self.wfile.write(msg.encode())

        except Exception as e:
            _log_event(
                logging.ERROR,
                "Unhandled proxy error",
                request_context=request_context,
                exc_info=True,
            )
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "internal error"}).encode())


class ThreadedHTTPServer(http.server.HTTPServer):
    """Handle each request in a new thread for concurrent streaming."""
    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._handle, args=(request, client_address))
        thread.daemon = True
        thread.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

HOST_HARDWARE_SPECS = _get_hardware_specs()
LOGGER, LOG_LISTENER = configure_logging()

def _ensure_tls_cert():
    cert = os.path.join(CHATS_DIR, "server.crt")
    key = os.path.join(CHATS_DIR, "server.key")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    os.makedirs(CHATS_DIR, exist_ok=True)
    # generate self-signed cert via openssl
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key, "-out", cert,
             "-days", "365", "-nodes", "-subj", "/CN=localhost"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _chmod_secure(cert)
        _chmod_secure(key)
    except Exception:
        return None, None
    return cert, key

def open_browser_delayed():
    """Open the browser after a short delay to ensure server is ready."""
    time.sleep(1.0)
    url = f"{'https' if HTTPS_MODE else 'http'}://localhost:{CHAT_SERVER_PORT}"
    if LAN_MODE and AUTH_TOKEN:
        url += f"?token={AUTH_TOKEN}"
    webbrowser.open(url)

def main():
    ensure_data_dir()
    _ensure_password()
    if HAS_PSUTIL:
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
    _set_active_log_mode(_load_settings_file().get("logMode"))

    # Announce this install to Hermes and keep reporting liveness/metrics.
    threading.Thread(target=_hermes_heartbeat_loop, daemon=True).start()
    _push_hermes_event("startup", _monitor_snapshot())

    # Try to find the local LAN IP
    local_ip = "127.0.0.1"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print()
    print("=" * 55)
    print("  ZenthaMini — Chat Server")
    print("=" * 55)
    print()
    scheme = "https" if HTTPS_MODE else "http"
    print(f"  Local Access:    {scheme}://localhost:{CHAT_SERVER_PORT}")
    print(f"  Network Access:  {scheme}://{local_ip}:{CHAT_SERVER_PORT}   <-- Use this on phone/other PC!")
    if CHAT_SERVER_PORT != 3333:
        print(f"  Note:            running on non-default port {CHAT_SERVER_PORT} (default 3333)")
    if LAN_MODE:
        print(f"  LAN token:       {AUTH_TOKEN}")
        print(f"  Pairing URL:     {scheme}://{local_ip}:{CHAT_SERVER_PORT}/?token={AUTH_TOKEN}")
    if _AUTH_PLAIN:
        print(f"  Login password:  {_AUTH_PLAIN}  (shown once; store securely)")
    print(f"  Ollama/Llama Proxy: {OLLAMA_HOST}")
    if LLAMA_CPP_MODE:
        print("  Running in LLAMA_CPP_MODE (Translating API requests)")
    print()
    print("  All chats auto-save to the USB drive!")
    print("  Press Ctrl+C to shut down.")
    print()
    print("-" * 55)

    bind_host = "0.0.0.0" if LAN_MODE else "127.0.0.1"
    server = ThreadedHTTPServer((bind_host, CHAT_SERVER_PORT), ChatHandler)
    if HTTPS_MODE:
        cert, key = _ensure_tls_cert()
        if cert and key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        else:
            print("  WARNING: could not generate TLS cert; running HTTP")

    # Open browser in background thread
    if not NO_BROWSER:
        threading.Thread(target=open_browser_delayed, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down chat server...")
        server.shutdown()
        print("  Goodbye!")
    finally:
        try:
            LOG_LISTENER.stop()
        except Exception:
            pass

if __name__ == "__main__":
    main()
