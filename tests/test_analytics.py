"""Analytics layer tests: scoring, entity graph, and finding ranking.

Synthetic bundles only. The point is that the reasoning holds, not that it matches this host.
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Shared"))
import analytics as A


def _raw(**over):
    raw = {
        "device": {"hostname": "box", "platform": "Linux", "ram_percent": 40},
        "network": {"ipv4": ["192.168.1.9"], "ipv6": ["fe80::1"], "dns": ["192.168.1.1"]},
        "listening_ports": [
            {"proto": "tcp", "address": "127.0.0.1:3333", "process": "chat_server.py"},
        ],
        "browser": {"profiles": [], "cache_mb": 0.0},
        "telemetry": {},
    }
    raw.update(over)
    return raw


def test_loopback_listener_is_not_exposure():
    r = A.analyze(_raw())
    assert r["scores"]["exposure"]["off_box_services"] == 0
    assert r["scores"]["exposure"]["index"] == 0


def test_off_box_listener_raises_exposure():
    r = A.analyze(_raw(listening_ports=[
        {"proto": "tcp", "address": "0.0.0.0:5900", "process": "x11vnc"},
    ]))
    exp = r["scores"]["exposure"]
    assert exp["off_box_services"] == 1
    assert exp["index"] > 0
    assert exp["top"][0]["known"] == "vnc"


def test_vnc_on_everything_is_high_severity():
    r = A.analyze(_raw(listening_ports=[
        {"proto": "tcp", "address": "0.0.0.0:5900", "process": "x11vnc"},
    ]))
    highs = [f for f in r["findings"] if f["severity"] == "high"]
    assert len(highs) == 1
    assert "5900" in highs[0]["title"]


def test_graph_links_service_and_host():
    r = A.analyze(_raw(listening_ports=[
        {"proto": "tcp", "address": "0.0.0.0:9100", "process": "font-preview"},
    ]))
    kinds = {e["kind"] for e in r["graph"]["edges"]}
    assert "runs_on" in kinds
    assert r["summary"]["relations"] >= 2


def test_tracker_domain_is_flagged_and_counted():
    r = A.analyze(_raw(browser={"profiles": [
        {"browser": "chromium",
         "history_top": [{"host": "doubleclick.net", "visits": 9}, {"host": "example.org", "visits": 3}]}],
        "cache_mb": 10.0}))
    assert "doubleclick.net" in r["scores"]["privacy"]["signals"]["tracker_domains"]
    assert "example.org" not in r["scores"]["privacy"]["signals"]["tracker_domains"]
    assert r["scores"]["privacy"]["index"] < 100


def test_public_dns_is_reported():
    r = A.analyze(_raw(network={"ipv4": [], "ipv6": [], "dns": ["1.1.1.1"]}))
    assert r["scores"]["privacy"]["signals"]["public_dns"] == ["1.1.1.1"]


def test_markdown_renders_findings():
    r = A.analyze(_raw(listening_ports=[
        {"proto": "tcp", "address": "0.0.0.0:5900", "process": "x11vnc"}]))
    md = A.render_markdown(r)
    assert md.startswith("# Device intelligence analysis")
    assert "[HIGH]" in md
    assert "Recommendation:" in md


def test_conversations_become_entities():
    r = A.analyze(_raw(), [{"id": "abc", "title": "Audit", "msgs": [1, 2], "model": "m"}])
    convs = [n for n in r["graph"]["nodes"] if n["type"] == "conversation"]
    assert len(convs) == 1 and convs[0]["id"] == "conv:abc"
