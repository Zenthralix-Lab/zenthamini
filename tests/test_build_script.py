"""The release builder must produce an artifact the updater will accept."""
import hashlib
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SHARED = os.path.join(ROOT, "Shared")

sys.path.insert(0, SHARED)

import maintenance as M  # noqa: E402


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_build_emits_stageable_tarball(tmp_path):
    result = subprocess.run(
        ["bash", "dist/build.sh", "--tar-only"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr

    with open(os.path.join(SHARED, "VERSION"), encoding="utf-8") as fh:
        version = fh.read().strip()
    name = "zenthmini_%s.tar.gz" % version
    built = os.path.join(ROOT, "dist", name)
    assert os.path.isfile(built)

    # Stage it exactly as the updater would, from an isolated install root.
    root = tmp_path / "root"
    (root / "dist").mkdir(parents=True)
    shutil.copy2(built, root / "dist" / name)
    digest = hashlib.sha256((root / "dist" / name).read_bytes()).hexdigest()
    (root / "dist" / "SHA256SUMS").write_text("%s  %s\n" % (digest, name),
                                              encoding="utf-8")

    staged = M.stage(root, tmp_path / "chats", artifact=name)
    assert staged["version"] == version
    files = set(staged["files"])
    assert {"Shared/chat_server.py", "Shared/maintenance.py"} <= files
    assert not any(p.startswith(("chat_data/", "logs/"))
                   or p.endswith((".pyc", "agent.token", ".tar.gz", ".deb", "SHA256SUMS"))
                   for p in files)
