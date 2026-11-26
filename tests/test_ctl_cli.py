"""CLI surface coverage: every documented subcommand must build a valid request."""
import contextlib
import importlib.util
import io
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "Shared", "scripts", "zentha_mini_ctl.py")
spec = importlib.util.spec_from_file_location("zentha_mini_ctl", SCRIPT)
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


def _run_cli(monkeypatch, argv):
    calls = []

    def fake_command(cfg, name, fields=None):
        calls.append((name, fields or {}))
        return {"ok": True}

    def fake_request(cfg, method, path, payload=None):
        calls.append((method + " " + path, payload or {}))
        return {"ok": True}

    monkeypatch.setattr(ctl, "_command", fake_command)
    monkeypatch.setattr(ctl, "_request", fake_request)
    with contextlib.redirect_stdout(io.StringIO()):
        assert ctl.main(["--config", "/tmp/opencode/zm-cli-test.json", *argv]) == 0
    return calls


def test_read_commands(monkeypatch):
    for cmd in ("status", "status-live", "doctor", "logs", "settings", "telemetry",
                "update-check", "update-status", "analytics"):
        calls = _run_cli(monkeypatch, [cmd])
        assert calls and calls[0][0], cmd


def test_write_commands_require_yes(monkeypatch):
    for cmd, extra in (("telemetry-clear", []), ("update-prepare", ["--version", "1.0.2"]),
                       ("update-apply", ["--version", "1.0.2"]), ("update-rollback", [])):
        try:
            _run_cli(monkeypatch, [cmd, *extra])
        except AssertionError:
            pass
        else:
            raise AssertionError(f"{cmd} must require --yes")
        calls = _run_cli(monkeypatch, [cmd, *extra, "--yes"])
        assert calls[0][1].get("confirm") is True, cmd


def test_shell_command_does_not_override_agent_command(monkeypatch):
    calls = _run_cli(monkeypatch, ["shell", "--command", "uptime"])
    name, fields = calls[0]
    assert name == "shell"
    assert fields["cmd"] == "uptime"
    assert "command" not in fields


def test_hermes_configure_reads_secret_from_file(monkeypatch, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("signed-secret\n", encoding="utf-8")
    calls = _run_cli(monkeypatch, ["hermes-configure", "--enabled", "true",
                                   "--secret-file", str(secret)])
    name, fields = calls[0]
    assert name == "settings_set"
    assert fields["settings"]["hermes"] == {"enabled": True, "secret": "signed-secret"}
