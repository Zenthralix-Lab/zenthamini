"""Palantir-style analytics over the device-intelligence sources.

The intel layer collects raw facts (device, network, ports, browser, chats). This module turns
those facts into an analysis: typed entities, the relations between them, a graph you can feed
to a viewer, weighted posture scores, and ranked findings with evidence.

Everything here is local and heuristic. It reads no new data and calls nothing off-box; it only
reasons about what the collector already gathered. Tracker and sensitive-port lists are small,
explicit, and labelled as heuristics, not ground truth.
"""

from __future__ import annotations

import re
from datetime import datetime

# ── Heuristic reference data ───────────────────────────────────────────────

# Hosts that exist to follow people around the web. Small and explicit on purpose: this is a
# nudge toward privacy-aware findings, not a claim of complete tracker coverage.
TRACKER_HINTS = (
    "doubleclick.net", "googlesyndication.com", "google-analytics.com", "analytics.google.com",
    "googletagmanager.com", "googleadservices.com", "adservice.google.com", "facebook.net",
    "connect.facebook.net", "ads-twitter.com", "analytics.twitter.com", "scorecardresearch.com",
    "quantserve.com", "outbrain.com", "taboola.com", "criteo.com", "hotjar.com",
    "fullstory.com", "segment.io", "mixpanel.com", "amplitude.com", "branch.io",
)

# Ports where an off-box listener is worth a harder look. Heuristic, weighted by bind address.
SENSITIVE_PORTS = {
    22: ("ssh", 3), 23: ("telnet", 5), 3389: ("rdp", 4), 5900: ("vnc", 5),
    445: ("smb", 4), 139: ("netbios", 3), 2049: ("nfs", 3), 3306: ("mysql", 3),
    5432: ("postgres", 3), 6379: ("redis", 4), 27017: ("mongodb", 4),
    9100: ("print/pjl", 4), 5601: ("kibana", 4), 11434: ("ollama", 2),
    5678: ("n8n", 4), 42000: ("pinokio", 3), 8080: ("http-alt", 2), 3333: ("http-alt", 2),
}

# Public DNS resolvers: fine, but they see every name you look up.
PUBLIC_DNS = {"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "208.67.222.222"}

LOOPBACK = ("127.", "::1", "[::1]")


def _is_loopback(address: str) -> bool:
    host = address.rsplit("%", 1)[0]
    if host.startswith(LOOPBACK):
        return True
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1] == "::1"
    return host in ("127.0.0.1", "::1", "localhost")


def _bind_scope(address: str) -> str:
    """Where a listener can be reached from: loopback, lan, or everywhere."""
    if _is_loopback(address):
        return "loopback"
    host = address
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    if host.startswith("0.0.0.0") or host == "::" or host == "":
        return "everywhere"
    if host.startswith(("10.", "192.168.")) or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host):
        return "lan"
    if host.startswith("169.254."):
        return "link-local"
    return "other"


def classify_domain(host: str) -> dict:
    """Cheap, honest domain profile: TLD, registrable-ish base, tracker hint."""
    h = (host or "").strip().lower().lstrip(".")
    if not h or "." not in h:
        return {"host": h, "tld": None, "base": h or None, "tracker": False}
    parts = h.split(".")
    tld = parts[-1]
    base = ".".join(parts[-2:]) if len(parts) >= 2 else h
    tracker = any(h == t or h.endswith("." + t) for t in TRACKER_HINTS)
    return {"host": h, "tld": tld, "base": base, "tracker": tracker}


def _port_number(address: str) -> int | None:
    host = address
    if host.startswith("[") and "]" in host:
        host = host[host.index("]") + 1:]
    if ":" in host:
        tail = host.rsplit(":", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return None


# ── Entity + relation builders ─────────────────────────────────────────────

def _entities(raw: dict, chats: list) -> list[dict]:
    device = raw.get("device") or {}
    network = raw.get("network") or {}
    hostname = device.get("hostname") or "local-host"
    host_id = "host:" + hostname

    out = [{"id": host_id, "type": "host", "label": hostname,
            "attrs": {k: device.get(k) for k in
                      ("platform", "release", "machine", "user", "cpu_logical", "ram_percent")}}]

    for ip in network.get("ipv4") or []:
        out.append({"id": "ip:" + ip, "type": "address", "label": ip,
                    "attrs": {"family": "ipv4", "scope": "lan" if ip.startswith(("10.", "192.168.")) else "other"}})
        out.append({"id": "rel:" + ip, "type": "_rel", "from": host_id, "to": "ip:" + ip, "kind": "has_address"})
    for ip in network.get("ipv6") or []:
        out.append({"id": "ip:" + ip, "type": "address", "label": ip,
                    "attrs": {"family": "ipv6", "scope": "link-local" if ip.startswith("fe80") else "global"}})
        out.append({"id": "rel:" + ip, "type": "_rel", "from": host_id, "to": "ip:" + ip, "kind": "has_address"})

    for dns in network.get("dns") or []:
        out.append({"id": "dns:" + dns, "type": "resolver", "label": dns,
                    "attrs": {"public": dns in PUBLIC_DNS}})
        out.append({"id": "rel:dns" + dns, "type": "_rel", "from": host_id, "to": "dns:" + dns, "kind": "uses_resolver"})

    for idx, p in enumerate(raw.get("listening_ports") or []):
        addr = p.get("address") or ""
        proc = (p.get("process") or "").strip()
        # ss renders the owner as users:(("name",pid=1,fd=2)); keep the readable part.
        m = re.search(r'"([^"]+)"', proc)
        if m:
            proc = m.group(1)
        port = _port_number(addr)
        label = addr if not SENSITIVE_PORTS.get(port, (None, 0))[0] else "%s (%s)" % (
            addr, SENSITIVE_PORTS[port][0])
        nid = "svc:%d" % idx
        out.append({"id": nid, "type": "service", "label": label,
                    "attrs": {"proto": p.get("proto"), "port": port, "bind": addr,
                              "scope": _bind_scope(addr), "process": proc,
                              "known": SENSITIVE_PORTS.get(port, (None, 0))[0]}})
        out.append({"id": "rel:svc%d" % idx, "type": "_rel", "from": nid, "to": host_id, "kind": "runs_on"})

    for prof in (raw.get("browser") or {}).get("profiles") or []:
        for h in prof.get("history_top") or []:
            c = classify_domain(h.get("host"))
            nid = "dom:" + c["host"]
            if not any(e["id"] == nid for e in out):
                out.append({"id": nid, "type": "domain", "label": c["host"],
                            "attrs": {"tld": c["tld"], "base": c["base"], "tracker": c["tracker"]}})
            out.append({"id": "rel:h%s" % nid, "type": "_rel", "from": host_id, "to": nid, "kind": "visited",
                        "attrs": {"visits": h.get("visits")}})

    for i, chat in enumerate(chats or []):
        if not isinstance(chat, dict):
            continue
        cid = chat.get("id")
        if not cid:
            continue
        nid = "conv:%s" % cid
        out.append({"id": nid, "type": "conversation", "label": chat.get("title") or "(untitled)",
                    "attrs": {"messages": len(chat.get("msgs") or []), "model": chat.get("model")}})
        out.append({"id": "rel:conv%s" % cid, "type": "_rel", "from": nid, "to": host_id, "kind": "stored_on"})

    return out


# ── Scoring ────────────────────────────────────────────────────────────────

def _exposure(entities: list[dict]) -> dict:
    services = [e for e in entities if e["type"] == "service"]
    weights = {"everywhere": 3, "other": 2, "link-local": 1, "lan": 1, "loopback": 0}
    score = 0
    worst = []
    for s in services:
        scope = s["attrs"].get("scope")
        w = weights.get(scope, 1)
        known_penalty = SENSITIVE_PORTS.get(s["attrs"].get("port"), (None, 0))[1]
        if scope == "loopback":
            continue
        contribution = w * (1 + known_penalty)
        score += contribution
        worst.append({"label": s["label"], "scope": scope, "process": s["attrs"].get("process"),
                      "known": s["attrs"].get("known"), "weight": contribution})
    worst.sort(key=lambda x: x["weight"], reverse=True)
    index = max(0, min(100, int(score * 2)))
    return {"index": index, "off_box_services": len(worst), "top": worst[:5],
            "loopback_only": len(services) - len(worst)}


def _privacy(raw: dict) -> dict:
    network = raw.get("network") or {}
    browser = raw.get("browser") or {}
    telemetry = raw.get("telemetry") or {}
    dns = network.get("dns") or []
    public_dns = [d for d in dns if d in PUBLIC_DNS]
    trackers = set()
    profiles = 0
    for prof in browser.get("profiles") or []:
        profiles += 1
        for h in prof.get("history_top") or []:
            c = classify_domain(h.get("host"))
            if c["tracker"]:
                trackers.add(c["host"])
    signals = {
        "public_dns": public_dns,
        "tracker_domains": sorted(trackers),
        "browser_profiles": profiles,
        "cache_mb": browser.get("cache_mb"),
        "telemetry_enabled": bool(telemetry),
    }
    penalties = 0
    penalties += 10 if public_dns else 0
    penalties += min(20, len(trackers) * 3)
    penalties += min(15, int((browser.get("cache_mb") or 0) / 50))
    penalties += 10 if telemetry else 0
    return {"index": max(0, 100 - penalties), "signals": signals}


# ── Findings ───────────────────────────────────────────────────────────────

def _findings(raw: dict, entities: list[dict], exposure: dict, privacy: dict) -> list[dict]:
    out = []
    network = raw.get("network") or {}
    browser = raw.get("browser") or {}
    device = raw.get("device") or {}

    for svc in exposure["top"]:
        if svc["scope"] == "everywhere":
            sev = "high" if svc["known"] in ("vnc", "telnet", "redis", "kibana", "n8n") else "medium"
            out.append({
                "severity": sev,
                "title": "Service reachable from any network: %s" % svc["label"],
                "evidence": "bind %s, process %s, known service %s"
                            % (svc["scope"], svc["process"] or "unknown", svc["known"] or "unrecognised"),
                "recommendation": "Bind to 127.0.0.1 unless it must be remote, and put a firewall in front.",
            })
        elif svc["scope"] == "lan":
            out.append({
                "severity": "low",
                "title": "Service reachable on the LAN: %s" % svc["label"],
                "evidence": "bind lan, process %s" % (svc["process"] or "unknown"),
                "recommendation": "Fine on a trusted network; confirm nothing else needs it exposed.",
            })

    if privacy["signals"]["public_dns"]:
        out.append({
            "severity": "low",
            "title": "Public DNS resolver in use",
            "evidence": ", ".join(privacy["signals"]["public_dns"]),
            "recommendation": "Every lookup is visible to that resolver. A local resolver or DNS-over-TLS reduces it.",
        })
    if privacy["signals"]["tracker_domains"]:
        out.append({
            "severity": "medium",
            "title": "%d tracker-style domains in browser history" % len(privacy["signals"]["tracker_domains"]),
            "evidence": ", ".join(privacy["signals"]["tracker_domains"][:5]),
            "recommendation": "Heuristic match only. Review in the browser's own protections if it matters to you.",
        })
    if privacy["signals"]["telemetry_enabled"]:
        out.append({
            "severity": "low",
            "title": "App telemetry is enabled",
            "evidence": "telemetry.json present and telemetry consent recorded",
            "recommendation": "Local only, but you can turn it off in the app's privacy settings.",
        })
    if (browser.get("cache_mb") or 0) > 100:
        out.append({
            "severity": "low",
            "title": "Browser cache is %.0f MB" % browser["cache_mb"],
            "evidence": "sum of chrome/chromium/firefox cache directories",
            "recommendation": "Cosmetic unless the disk is tight.",
        })
    if device.get("ram_percent", 0) and device["ram_percent"] > 85:
        out.append({
            "severity": "medium",
            "title": "Memory pressure: %s%% used" % device["ram_percent"],
            "evidence": "sampled at collection time",
            "recommendation": "A local model plus a browser can saturate this box; check the biggest consumers.",
        })
    ipv6_global = [e["label"] for e in entities
                   if e["type"] == "address" and e["attrs"].get("scope") == "global"]
    if ipv6_global:
        out.append({
            "severity": "low",
            "title": "%d global IPv6 address(es)" % len(ipv6_global),
            "evidence": ", ".join(ipv6_global[:3]),
            "recommendation": "IPv6 often bypasses IPv4 firewall habits. Confirm the host firewall covers it.",
        })
    if not network.get("dns"):
        out.append({
            "severity": "medium",
            "title": "No DNS servers readable",
            "evidence": "/etc/resolv.conf had no nameserver line",
            "recommendation": "Names may resolve through systemd-resolved; worth confirming rather than assuming.",
        })

    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: order.get(f["severity"], 3))
    return out


# ── Entry point ────────────────────────────────────────────────────────────

def analyze(raw: dict, chats: list | None = None) -> dict:
    """Build the full analysis from a raw intel bundle."""
    entities = _entities(raw, chats or [])
    relations = [e for e in entities if e["type"] == "_rel"]
    nodes = [e for e in entities if e["type"] != "_rel"]
    exposure = _exposure(nodes)
    privacy = _privacy(raw)
    findings = _findings(raw, nodes, exposure, privacy)

    counts = {}
    for n in nodes:
        counts[n["type"]] = counts.get(n["type"], 0) + 1

    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "entity_counts": counts,
        "graph": {
            "nodes": [{"id": n["id"], "type": n["type"], "label": n["label"],
                       "attrs": n["attrs"]} for n in nodes],
            "edges": [{"from": r["from"], "to": r["to"], "kind": r["kind"],
                       "attrs": r.get("attrs")} for r in relations],
        },
        "scores": {
            "exposure": exposure,
            "privacy": privacy,
        },
        "findings": findings,
        "summary": {
            "entities": len(nodes),
            "relations": len(relations),
            "findings": len(findings),
            "high": high,
            "medium": medium,
            "low": len(findings) - high - medium,
        },
    }


def render_markdown(report: dict) -> str:
    """Plain-text rendering for chat or a file. No emoji, no filler."""
    s = report.get("summary") or {}
    sc = report.get("scores") or {}
    lines = []
    lines.append("# Device intelligence analysis")
    lines.append("")
    lines.append("Generated: %s" % report.get("generated_at"))
    lines.append("")
    lines.append("Entities: %s   Relations: %s   Findings: %s (high %s, medium %s, low %s)"
                 % (s.get("entities"), s.get("relations"), s.get("findings"),
                    s.get("high"), s.get("medium"), s.get("low")))
    lines.append("")
    lines.append("## Posture")
    lines.append("")
    lines.append("- Exposure index: %s/100, %s off-box service(s)"
                 % ((sc.get("exposure") or {}).get("index"),
                    (sc.get("exposure") or {}).get("off_box_services")))
    lines.append("- Privacy index: %s/100" % (sc.get("privacy") or {}).get("index"))
    lines.append("- Entities by type: %s" % ", ".join(
        "%s %s" % (k, v) for k, v in (report.get("entity_counts") or {}).items()))
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    for f in report.get("findings") or []:
        lines.append("### [%s] %s" % (f["severity"].upper(), f["title"]))
        lines.append("Evidence: %s" % f["evidence"])
        lines.append("Recommendation: %s" % f["recommendation"])
        lines.append("")
    top = (sc.get("exposure") or {}).get("top") or []
    if top:
        lines.append("## Highest-weight listeners")
        lines.append("")
        for t in top:
            lines.append("- %s, bind %s, process %s, known %s"
                         % (t["label"], t["scope"], t["process"] or "unknown", t["known"] or "unrecognised"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
