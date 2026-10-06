import subprocess
from pathlib import Path

import pytest

from test_run_report import load_module


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    other = tmp_path / "other"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "clone", str(remote), str(local))
    git(local, "checkout", "-b", "main")
    git(local, "config", "user.email", "test@example.org")
    git(local, "config", "user.name", "test")
    (local / "data").mkdir()
    (local / "data" / "catalog.json").write_text("old")
    git(local, "add", ".")
    git(local, "commit", "-m", "initial")
    git(local, "push", "origin", "main")
    git(tmp_path, "clone", "-b", "main", str(remote), str(other))
    git(other, "config", "user.email", "test@example.org")
    git(other, "config", "user.name", "test")
    return local, other


def test_integrates_concurrent_code_without_losing_state(repos, monkeypatch):
    local, other = repos
    (other / "code.txt").write_text("new code")
    git(other, "add", ".")
    git(other, "commit", "-m", "code")
    git(other, "push", "origin", "main")
    (local / "data" / "catalog.json").write_text("new state")
    monkeypatch.chdir(local)
    module = load_module("publish_state")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module.publish("state")
    assert (local / "code.txt").read_text() == "new code"
    assert git(other, "show", "origin/main:data/catalog.json") == "old"  # Other clone has not fetched.
    git(other, "fetch", "origin")
    assert git(other, "show", "origin/main:data/catalog.json") == "new state"


def test_never_overwrites_concurrent_remote_state(repos, monkeypatch):
    local, other = repos
    (other / "data" / "catalog.json").write_text("remote state")
    git(other, "add", ".")
    git(other, "commit", "-m", "remote data")
    git(other, "push", "origin", "main")
    (local / "data" / "catalog.json").write_text("local state")
    monkeypatch.chdir(local)
    with pytest.raises(RuntimeError, match="estado remoto cambio"):
        load_module("publish_state").publish("local data")
    git(other, "fetch", "origin")
    assert git(other, "show", "origin/main:data/catalog.json") == "remote state"
