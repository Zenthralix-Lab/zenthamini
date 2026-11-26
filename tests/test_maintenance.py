"""Maintenance helper tests: verified staging, reversible apply, safe inputs."""
import hashlib
import io
import json
import os
import sys
import tarfile

import pytest

SHARED = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Shared"))
sys.path.insert(0, SHARED)

import maintenance as M  # noqa: E402


def _tarball(root, version="1.0.2", files=None, checksum=True):
    dist = M.dist_dir(root)
    dist.mkdir(parents=True, exist_ok=True)
    name = "zenthmini_%s.tar.gz" % version
    files = files if files is not None else {
        "Shared/chat_server.py": b"print('new')\n",
        "VERSION": (version + "\n").encode(),
        "README.md": b"new docs\n",
        "chat_data/agent.token": b"must-not-ship\n",
        "Shared/chat_data/agent.token": b"must-not-ship\n",
        "../escape.py": b"must-not-ship\n",
    }
    with tarfile.open(dist / name, "w:gz") as tf:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o755 if path.endswith(".sh") else 0o644
            tf.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256((dist / name).read_bytes()).hexdigest()
    if checksum:
        (dist / "SHA256SUMS").write_text("%s  %s\n" % (digest, name), encoding="utf-8")
    return name


def test_stage_excludes_runtime_and_unsafe_paths(tmp_path):
    root = tmp_path / "root"
    chats = tmp_path / "chats"
    name = _tarball(root)
    result = M.stage(root, chats, artifact=name)
    assert result["version"] == "1.0.2" and result["restart_required"] is True
    staged = set(result["files"])
    assert staged == {"Shared/chat_server.py", "VERSION", "README.md"}
    assert not any("agent.token" in p or "escape" in p for p in staged)
    manifest = json.loads((M.latest_staged(chats)[1] / "manifest.json").read_text())
    assert manifest["artifact_sha256"] == result["sha256"]


def test_apply_and_rollback_are_reversible(tmp_path):
    root = tmp_path / "root"
    chats = tmp_path / "chats"
    target = root / "Shared" / "chat_server.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('old')\n", encoding="utf-8")
    (root / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    name = _tarball(root)
    M.stage(root, chats, artifact=name)
    applied = M.apply_staged(root, chats)
    assert applied["files_updated"] == 3 and applied["backed_up"] == 2
    assert target.read_text() == "print('new')\n"
    assert (root / "VERSION").read_text() == "1.0.2\n"
    rolled = M.rollback(root, chats)
    assert rolled["restored"] == 2 and rolled["removed"] == 1
    assert target.read_text() == "print('old')\n"
    assert (root / "VERSION").read_text() == "1.0.1\n"
    assert not (root / "README.md").exists()
    with pytest.raises(M.MaintenanceError):
        M.rollback(root, chats)


def test_checksum_is_mandatory(tmp_path):
    root = tmp_path / "root"
    name = _tarball(root, checksum=False)
    with pytest.raises(M.MaintenanceError, match="SHA256SUMS"):
        M.stage(root, tmp_path / "chats", artifact=name)
    sums = M.dist_dir(root) / "SHA256SUMS"
    sums.write_text("0" * 64 + "  " + name + "\n", encoding="utf-8")
    with pytest.raises(M.MaintenanceError, match="checksum mismatch"):
        M.stage(root, tmp_path / "chats", artifact=name)


def test_artifact_name_must_be_basename(tmp_path):
    with pytest.raises(M.MaintenanceError, match="basename"):
        M.resolve_artifact(tmp_path, artifact="../evil.tar.gz")
    with pytest.raises(M.MaintenanceError, match="basename"):
        M.resolve_artifact(tmp_path, artifact="anything.zip")


def test_deb_is_refused(tmp_path):
    root = tmp_path / "root"
    dist = M.dist_dir(root)
    dist.mkdir(parents=True)
    name = "zenthmini_1.0.2_all.deb"
    (dist / name).write_bytes(b"fake")
    (dist / "SHA256SUMS").write_text(
        hashlib.sha256(b"fake").hexdigest() + "  " + name + "\n", encoding="utf-8")
    with pytest.raises(M.MaintenanceError, match="portable"):
        M.resolve_artifact(root, artifact=name)


def _github_release(name, data, digest=None, extra_assets=None):
    return [{
        "tag_name": "v1.0.3", "draft": False, "prerelease": False,
        "html_url": "https://github.com/o/r/releases/tag/v1.0.3",
        "body": "release notes", "published_at": "2026-11-27T00:00:00Z",
        "assets": [{"name": name, "size": len(data), "digest": digest or "",
                    "browser_download_url": "https://example.test/" + name}]
                   + list(extra_assets or []),
    }]


def test_download_release_verifies_digest_and_stages(tmp_path, monkeypatch):
    build = tmp_path / "build"
    name = _tarball(build, version="1.0.3")
    data = (M.dist_dir(build) / name).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(M, "_github_json", lambda url, token="", timeout=20:
                        _github_release(name, data, "sha256:" + digest))
    monkeypatch.setattr(M, "_fetch_asset", lambda url, token="", timeout=300,
                        max_bytes=M.MAX_ARTIFACT_BYTES: data)
    root, chats = tmp_path / "root", tmp_path / "chats"
    result = M.download_release(root, chats)
    assert result["source"] == "github" and result["version"] == "1.0.3"
    assert result["checksum_source"] == "asset-digest"
    assert "Shared/chat_server.py" in result["files"]
    assert M.read_checksums(root)[name] == digest
    assert M.latest_staged(chats)[0]["artifact"] == name


def test_download_release_accepts_sha256sums_asset(tmp_path, monkeypatch):
    build = tmp_path / "build"
    name = _tarball(build, version="1.0.3")
    data = (M.dist_dir(build) / name).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    sums = ("%s  %s\n" % (digest, name)).encode()
    sums_asset = {"name": "SHA256SUMS", "size": len(sums), "digest": "",
                  "browser_download_url": "https://example.test/SHA256SUMS"}
    monkeypatch.setattr(M, "_github_json", lambda url, token="", timeout=20:
                        _github_release(name, data, "", [sums_asset]))
    monkeypatch.setattr(M, "_fetch_asset", lambda url, token="", timeout=300,
                        max_bytes=M.MAX_ARTIFACT_BYTES:
                        sums if url.endswith("SHA256SUMS") else data)
    result = M.download_release(tmp_path / "root", tmp_path / "chats")
    assert result["checksum_source"] == "SHA256SUMS" and result["version"] == "1.0.3"


def test_download_release_rejects_bad_checksum(tmp_path, monkeypatch):
    build = tmp_path / "build"
    name = _tarball(build, version="1.0.3")
    data = (M.dist_dir(build) / name).read_bytes()
    monkeypatch.setattr(M, "_github_json", lambda url, token="", timeout=20:
                        _github_release(name, data, "sha256:" + "0" * 64))
    monkeypatch.setattr(M, "_fetch_asset", lambda url, token="", timeout=300,
                        max_bytes=M.MAX_ARTIFACT_BYTES: data)
    with pytest.raises(M.MaintenanceError, match="checksum mismatch"):
        M.download_release(tmp_path / "root", tmp_path / "chats")


def test_download_release_requires_published_checksum(tmp_path, monkeypatch):
    build = tmp_path / "build"
    name = _tarball(build, version="1.0.3")
    data = (M.dist_dir(build) / name).read_bytes()
    monkeypatch.setattr(M, "_github_json", lambda url, token="", timeout=20:
                        _github_release(name, data))
    with pytest.raises(M.MaintenanceError, match="no SHA-256"):
        M.download_release(tmp_path / "root", tmp_path / "chats")


def test_fetch_asset_asks_for_binary_only_from_the_api(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            return b"data"

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return Response()

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    M._fetch_asset("https://api.github.com/repos/o/r/releases/assets/1")
    M._fetch_asset("https://github.com/o/r/releases/download/v1/zenthmini_1.0.3.tar.gz")
    assert seen[0].get_header("Accept") == "application/octet-stream"
    assert seen[1].get_header("Accept") == "*/*"


def test_update_state_reports_artifacts(tmp_path):
    root = tmp_path / "root"
    name = _tarball(root)
    state = M.update_state(root, tmp_path / "chats")
    assert state["artifacts"][0]["artifact"] == name
    assert state["artifacts"][0]["checksum_ok"] is True
    assert state["staged"] == [] and state["last_applied"] is None
