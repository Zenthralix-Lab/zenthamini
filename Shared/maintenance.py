"""Verified, reversible maintenance helpers for the ZenthaMini agent API.

Used by ``chat_server.py`` for the ``update_*`` and ``maintenance_status``
commands. Only releases built by ``dist/build.sh`` (with a matching
``dist/SHA256SUMS`` entry) can be staged or applied. Runtime data
(chats, logs, models, engines, tokens, TLS keys) is never part of an update.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ARTIFACT_RE = re.compile(
    r"^zenthmini_(\d+\.\d+\.\d+(?:[-.][A-Za-z0-9]+)*)(?:_all)?\.(tar\.gz|deb)$"
)
BLOCKED_TOP = {
    "chat_data", "logs", "models", "bin", "lib", ".engines", "vendor",
    "__pycache__", ".pytest_cache", "python",
}
SENSITIVE_NAMES = {"agent.token", "auth.token", "settings.json", "telemetry.json",
                   "server.key", "server.crt"}
ALLOWED_SUFFIXES = {
    ".py", ".md", ".html", ".json", ".css", ".js", ".mjs", ".sh", ".command",
    ".bat", ".ps1", ".yml", ".yaml", ".woff", ".woff2", ".txt",
}
ALLOWED_NAMES = {"VERSION", "LICENSE", ".gitignore"}
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
GITHUB_API = "https://api.github.com"
DEFAULT_GITHUB_REPO = "zenthralixlab-png/zenthamini"
RELEASE_NOTES_LIMIT = 8000


class MaintenanceError(Exception):
    """User-facing error raised for unsafe or impossible maintenance actions."""


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def version_key(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version))


def dist_dir(root: str | Path) -> Path:
    return Path(root) / "dist"


def updates_dir(chats_dir: str | Path) -> Path:
    return Path(chats_dir) / "updates"


def staged_dir(chats_dir: str | Path) -> Path:
    return updates_dir(chats_dir) / "staged"


def backups_dir(chats_dir: str | Path) -> Path:
    return updates_dir(chats_dir) / "backups"


def marker_path(root: str | Path) -> Path:
    return Path(root) / ".zentha-update-applied.json"


def _parse_checksums(text: str) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            out[os.path.basename(parts[1].strip())] = parts[0]
    return out


def read_checksums(root: str | Path) -> dict[str, str]:
    path = dist_dir(root) / "SHA256SUMS"
    if not path.is_file():
        return {}
    return _parse_checksums(path.read_text(encoding="utf-8"))


def _record_checksum(root: str | Path, name: str, digest: str) -> None:
    """Merge one verified artifact into dist/SHA256SUMS for stage() to trust."""
    sums = read_checksums(root)
    sums[name] = digest
    path = dist_dir(root) / "SHA256SUMS"
    path.write_text("".join("%s  %s\n" % (sums[n], n) for n in sorted(sums)),
                    encoding="utf-8")


def github_repo(slug: str | None = None) -> str:
    """owner/name of the release repository (ZENTHA_UPDATE_REPO overrides)."""
    value = (slug or os.environ.get("ZENTHA_UPDATE_REPO") or DEFAULT_GITHUB_REPO).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise MaintenanceError("github repo must look like owner/name")
    return value


def _github_headers(token: str = "", accept: str = "application/vnd.github+json") -> dict:
    headers = {"User-Agent": "ZenthaMini", "Accept": accept}
    token = token or os.environ.get("ZENTHA_GITHUB_TOKEN") or ""
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _github_json(url: str, token: str = "", timeout: int = 20):
    req = urllib.request.Request(url, headers=_github_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise MaintenanceError("no GitHub releases published yet")
        raise MaintenanceError("GitHub release lookup failed: HTTP %s" % exc.code)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise MaintenanceError("GitHub release lookup failed: %s" % exc)


def _fetch_asset(url: str, token: str = "", timeout: int = 300,
                 max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes:
    # Only the REST API needs the binary Accept header. GitHub's release CDN
    # answers that variant with a stale copy of a re-uploaded asset, which
    # then fails checksum verification, so downloads go out with a plain Accept.
    api = urllib.parse.urlsplit(url).hostname == "api.github.com"
    headers = _github_headers(token, "application/octet-stream" if api else "*/*")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise MaintenanceError("cannot download release asset: %s" % exc)
    if len(data) > max_bytes:
        raise MaintenanceError("release asset is larger than the %s MB limit"
                               % (max_bytes // (1024 * 1024)))
    return data


def _release_version(release: dict) -> str:
    tag = str(release.get("tag_name") or release.get("name") or "")
    match = re.search(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9]+)*", tag)
    return match.group(0) if match else ""


def github_releases(repo: str | None = None, token: str = "",
                    include_prerelease: bool = False, limit: int = 30) -> list[dict]:
    """Published releases, newest first, with the assets ZenthaMini can install."""
    repo = github_repo(repo)
    url = "%s/repos/%s/releases?per_page=%d" % (GITHUB_API, repo, max(1, min(int(limit), 100)))
    data = _github_json(url, token)
    if not isinstance(data, list):
        raise MaintenanceError("unexpected GitHub response")
    out = []
    for release in data:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if release.get("prerelease") and not include_prerelease:
            continue
        version = _release_version(release)
        if not version:
            continue
        assets = []
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict) or not asset.get("name"):
                continue
            assets.append({
                "name": asset["name"],
                "size": asset.get("size") or 0,
                "digest": asset.get("digest") or "",
                "url": asset.get("browser_download_url") or asset.get("url") or "",
            })
        out.append({
            "version": version,
            "tag": release.get("tag_name") or "",
            "name": release.get("name") or version,
            "release_url": release.get("html_url") or "https://github.com/%s/releases" % repo,
            "notes": (release.get("body") or "")[:RELEASE_NOTES_LIMIT],
            "published_at": release.get("published_at") or "",
            "prerelease": bool(release.get("prerelease")),
            "assets": assets,
        })
    if not out:
        raise MaintenanceError("no published releases for %s" % repo)
    return sorted(out, key=lambda rel: version_key(rel["version"]), reverse=True)


def github_latest_release(repo: str | None = None, token: str = "",
                          include_prerelease: bool = False) -> dict:
    return github_releases(repo, token, include_prerelease)[0]


def _release_checksum(release: dict, asset: dict, token: str = "") -> tuple[str, str]:
    """Return (sha256, source). Refuse releases that publish no checksum."""
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", digest[7:]):
        return digest[7:], "asset-digest"
    for other in release.get("assets") or []:
        if str(other.get("name") or "").upper() == "SHA256SUMS" and other.get("url"):
            data = _fetch_asset(other["url"], token, timeout=30, max_bytes=1024 * 1024)
            published = _parse_checksums(data.decode("utf-8", "replace"))
            if published.get(asset["name"]):
                return published[asset["name"]], "SHA256SUMS"
    raise MaintenanceError(
        "release publishes no SHA-256 for %s; upload a SHA256SUMS asset or a digest"
        % asset["name"])


def download_release(root: str | Path, chats_dir: str | Path, repo: str | None = None,
                     version: str | None = None, token: str = "",
                     asset: str | None = None) -> dict:
    """Download a release tarball from GitHub, verify it, then stage it."""
    repo = github_repo(repo)
    releases = github_releases(repo, token, include_prerelease=False)
    if version:
        wanted = version[1:] if version.lower().startswith("v") else version
        releases = [rel for rel in releases if rel["version"] == wanted]
        if not releases:
            raise MaintenanceError("release %s not found on GitHub" % version)
    release = releases[0]
    candidates = [a for a in release["assets"]
                  if ARTIFACT_RE.match(a["name"]) and a["name"].endswith(".tar.gz")]
    if asset:
        if os.path.basename(asset) != asset:
            raise MaintenanceError("asset must be a basename")
        candidates = [a for a in candidates if a["name"] == asset]
    if not candidates:
        raise MaintenanceError(
            "release %s has no portable .tar.gz asset (found: %s)"
            % (release["version"], ", ".join(a["name"] for a in release["assets"]) or "none"))
    chosen = sorted(candidates, key=lambda a: a["size"])[-1]
    if chosen["size"] and chosen["size"] > MAX_ARTIFACT_BYTES:
        raise MaintenanceError("release asset is larger than the allowed limit")
    expected, checksum_source = _release_checksum(release, chosen, token)
    data = _fetch_asset(chosen["url"], token)
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        raise MaintenanceError("checksum mismatch for %s; refusing the download" % chosen["name"])
    dist = dist_dir(root)
    dist.mkdir(parents=True, exist_ok=True)
    tmp = dist / (chosen["name"] + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dist / chosen["name"])
    _record_checksum(root, chosen["name"], digest)
    result = stage(root, chats_dir, artifact=chosen["name"], version=release["version"])
    result.update({
        "source": "github",
        "repo": repo,
        "release_url": release["release_url"],
        "release_notes": release["notes"],
        "downloaded_from": chosen["url"],
        "checksum_source": checksum_source,
    })
    return result


def list_artifacts(root: str | Path) -> list[dict]:
    d = dist_dir(root)
    sums = read_checksums(root)
    out = []
    if not d.is_dir():
        return out
    for path in sorted(d.iterdir()):
        m = ARTIFACT_RE.match(path.name)
        if not m or not path.is_file():
            continue
        digest = _sha256(path) if path.stat().st_size <= MAX_ARTIFACT_BYTES else ""
        out.append({
            "artifact": path.name,
            "version": m.group(1),
            "kind": "tarball" if m.group(2) == "tar.gz" else "deb",
            "size": path.stat().st_size,
            "sha256": digest,
            "checksum_ok": bool(digest and sums.get(path.name) == digest),
        })
    return out


def resolve_artifact(root: str | Path, artifact: str | None = None,
                     version: str | None = None) -> dict:
    """Pick a release artifact and verify it against dist/SHA256SUMS."""
    if artifact and (Path(artifact).name != artifact or not ARTIFACT_RE.match(artifact)):
        raise MaintenanceError("artifact must be a basename like zenthmini_1.0.2.tar.gz")
    candidates = list_artifacts(root)
    if artifact:
        candidates = [a for a in candidates if a["artifact"] == artifact]
    if version:
        candidates = [a for a in candidates if a["version"] == version]
    if not candidates:
        raise MaintenanceError("no matching artifact found in dist/")
    tarballs = [a for a in candidates if a["kind"] == "tarball"]
    chosen = sorted(tarballs or candidates, key=lambda a: version_key(a["version"]))[-1]
    if chosen["kind"] == "deb":
        raise MaintenanceError("only the portable .tar.gz artifact can be applied")
    if not read_checksums(root):
        raise MaintenanceError("dist/SHA256SUMS missing; refusing unverified artifact")
    if not chosen["checksum_ok"]:
        raise MaintenanceError("checksum mismatch for %s; rebuild with dist/build.sh" % chosen["artifact"])
    return chosen


def _safe_members(tf: tarfile.TarFile) -> list[tuple[tarfile.TarInfo, str]]:
    """Only regular, in-tree source files; nothing runtime or sensitive."""
    selected = []
    for member in tf.getmembers():
        name = member.name.replace("\\", "/")
        while name.startswith("./"):
            name = name[2:]
        if name.startswith("ZenthaMini/"):
            name = name[len("ZenthaMini/"):]
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if not parts or ".." in parts or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            continue
        if parts[0] in BLOCKED_TOP or any(p in BLOCKED_TOP for p in parts[:-1]):
            continue
        if Path(name).name in SENSITIVE_NAMES:
            continue
        suffix = Path(name).suffix.lower()
        if suffix and suffix not in ALLOWED_SUFFIXES and Path(name).name not in ALLOWED_NAMES:
            continue
        if not suffix and Path(name).name not in ALLOWED_NAMES:
            continue
        if not (member.isfile() or member.isdir()):
            continue
        if member.isdir():
            continue
        selected.append((member, "/".join(parts)))
    return selected


def stage(root: str | Path, chats_dir: str | Path, artifact: str | None = None,
          version: str | None = None) -> dict:
    """Verify a release tarball and unpack its source files into staging."""
    chosen = resolve_artifact(root, artifact, version)
    path = dist_dir(root) / chosen["artifact"]
    base = staged_dir(chats_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / ("%s_%s" % (chosen["version"], str(int(time.time() * 1000))))
    target.mkdir(parents=True, exist_ok=False)
    files = []
    try:
        with tarfile.open(path, "r:gz") as tf:
            for member, rel in _safe_members(tf):
                stream = tf.extractfile(member)
                if stream is None:
                    continue
                data = stream.read()
                if len(data) > 20 * 1024 * 1024:
                    raise MaintenanceError("file too large in artifact: %s" % rel)
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                os.chmod(dest, member.mode & 0o777 or 0o644)
                files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(),
                              "mode": member.mode & 0o777 or 0o644})
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise MaintenanceError("cannot unpack artifact: %s" % exc)
    if not files:
        shutil.rmtree(target, ignore_errors=True)
        raise MaintenanceError("artifact contains no updatable source files")
    manifest = {
        "version": chosen["version"],
        "artifact": chosen["artifact"],
        "artifact_sha256": chosen["sha256"],
        "prepared_at": _now(),
        "staging_dir": str(target),
        "files": files,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "ok": True, "version": chosen["version"], "artifact": chosen["artifact"],
        "sha256": chosen["sha256"], "staging_dir": str(target),
        "files": [f["path"] for f in files], "restart_required": True,
    }


def staged_manifests(chats_dir: str | Path) -> list[tuple[dict, Path]]:
    base = staged_dir(chats_dir)
    out = []
    if not base.is_dir():
        return out
    for manifest in base.glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") and data.get("files"):
                out.append((data, manifest.parent))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda pair: pair[0].get("prepared_at", ""))


def latest_staged(chats_dir: str | Path, version: str | None = None) -> tuple[dict, Path]:
    found = staged_manifests(chats_dir)
    if version:
        found = [f for f in found if f[0]["version"] == version]
    if not found:
        raise MaintenanceError("no staged artifact; run update_prepare first")
    return found[-1]


def apply_staged(root: str | Path, chats_dir: str | Path,
                 version: str | None = None) -> dict:
    """Install a staged release with a per-file backup for rollback."""
    root = Path(root)
    manifest, staging = latest_staged(chats_dir, version)
    verified = 0
    for entry in manifest["files"]:
        src = staging / entry["path"]
        if not src.is_file() or _sha256(src) != entry["sha256"]:
            raise MaintenanceError("staged file failed verification: %s" % entry["path"])
        verified += 1
    for entry in manifest["files"]:
        rel = entry["path"]
        if Path(rel).parts[0] in BLOCKED_TOP or Path(rel).name in SENSITIVE_NAMES:
            raise MaintenanceError("refusing to overwrite protected path: %s" % rel)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
    backup = backups_dir(chats_dir) / stamp
    backup.mkdir(parents=True, exist_ok=False)
    applied = []
    for entry in manifest["files"]:
        rel = entry["path"]
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        record = {"path": rel, "mode": entry["mode"], "existed": dest.exists()}
        if dest.exists():
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, saved)
            record["backup_mode"] = dest.stat().st_mode & 0o777
        data = (staging / rel).read_bytes()
        tmp = dest.with_name(dest.name + ".zentha-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        os.chmod(dest, entry["mode"])
        applied.append(record)
    marker = {
        "version": manifest["version"],
        "artifact": manifest["artifact"],
        "applied_at": _now(),
        "backup_dir": str(backup),
        "staging_dir": str(staging),
        "files": applied,
    }
    marker_path(root).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return {
        "ok": True, "version": manifest["version"], "files_updated": len(applied),
        "backed_up": sum(1 for f in applied if f["existed"]),
        "backup_dir": str(backup), "restart_required": True,
    }


def rollback(root: str | Path, chats_dir: str | Path) -> dict:
    """Restore the files touched by the last apply. Idempotent-safe."""
    root = Path(root)
    mp = marker_path(root)
    if not mp.is_file():
        raise MaintenanceError("no applied update to roll back")
    try:
        marker = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MaintenanceError("unreadable update marker: %s" % exc)
    if marker.get("rolled_back_at"):
        raise MaintenanceError("last update was already rolled back")
    backup = Path(marker.get("backup_dir", ""))
    restored, removed = 0, 0
    for entry in marker.get("files", []):
        rel = entry["path"]
        parts = Path(rel).parts
        if parts[0] in BLOCKED_TOP or Path(rel).name in SENSITIVE_NAMES:
            continue
        dest = root / rel
        saved = backup / rel
        if entry.get("existed") and saved.is_file():
            tmp = dest.with_name(dest.name + ".zentha-tmp")
            shutil.copy2(saved, tmp)
            os.replace(tmp, dest)
            os.chmod(dest, entry.get("backup_mode", 0o644))
            restored += 1
        elif not entry.get("existed") and dest.is_file():
            dest.unlink()
            removed += 1
    marker["rolled_back_at"] = _now()
    mp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return {"ok": True, "version": marker.get("version"), "restored": restored,
            "removed": removed, "restart_required": True}


def update_state(root: str | Path, chats_dir: str | Path) -> dict:
    state = {"artifacts": list_artifacts(root), "staged": [], "last_applied": None}
    state["staged"] = [
        {"version": m["version"], "artifact": m["artifact"],
         "prepared_at": m["prepared_at"], "files": len(m["files"]),
         "staging_dir": str(p)}
        for m, p in staged_manifests(chats_dir)
    ]
    mp = marker_path(root)
    if mp.is_file():
        try:
            marker = json.loads(mp.read_text(encoding="utf-8"))
            state["last_applied"] = {
                "version": marker.get("version"),
                "applied_at": marker.get("applied_at"),
                "rolled_back_at": marker.get("rolled_back_at"),
                "files": len(marker.get("files", [])),
            }
        except (OSError, ValueError):
            state["last_applied"] = {"error": "unreadable update marker"}
    return state
