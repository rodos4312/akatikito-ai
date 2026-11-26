"""D4 Controlled Git commit & draft PR tests: dev profile gate, secret block, check gate, no push."""
from __future__ import annotations

import subprocess

import pytest

from akatikito.git_workflow import execute_commit, prepare_commit, prepare_draft_pr, validate_git_args
from akatikito.settings import Settings


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@akatikito.local"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Akatikito Test"], cwd=str(path), check=True, capture_output=True)
    (path / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(path), check=True, capture_output=True)


def test_forbidden_git_commands_and_gates_are_blocked(tmp_path):
    for bad in (["push", "origin", "main"], ["commit", "--no-verify"], ["reset", "--hard"], ["push", "--force"]):
        with pytest.raises(PermissionError):
            validate_git_args(bad)

    closed = Settings(advanced_features=[])
    with pytest.raises(PermissionError, match="Advanced"):
        prepare_commit(tmp_path, closed, project_profile="dev")

    opened = Settings(advanced_features=["git"])
    with pytest.raises(PermissionError, match="dev"):
        prepare_commit(tmp_path, opened, project_profile="read_only")


def test_controlled_commit_blocks_secrets_and_failed_checks_then_commits_on_approval(tmp_path):
    _init_repo(tmp_path)
    cfg = Settings(advanced_features=["git"])

    # Secret filename (.env) is blocked
    (tmp_path / ".env").write_text("SECRET=123\n", encoding="utf-8")
    blocked_env = prepare_commit(tmp_path, cfg, project_profile="dev", files=[".env"])
    assert blocked_env["ok"] is False and blocked_env["secret_findings"]
    (tmp_path / ".env").unlink()

    # Secret API key inside code file is blocked
    (tmp_path / "app.py").write_text('API_KEY = "sk-1234567890abcdefghijklmnop"\n', encoding="utf-8")
    blocked_key = prepare_commit(tmp_path, cfg, project_profile="dev", files=["app.py"])
    assert blocked_key["ok"] is False and blocked_key["secret_findings"]

    # Clean edit but failing verification check is blocked
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    failed_chk = prepare_commit(tmp_path, cfg, project_profile="dev", files=["app.py"], verifier=lambda _: (False, "1 failed"))
    assert failed_chk["ok"] is False and "checks" in failed_chk["error"]

    # Without user_approved=True -> refused
    with pytest.raises(PermissionError, match="έγκριση"):
        execute_commit(tmp_path, cfg, "fix: update x", project_profile="dev", files=["app.py"], user_approved=False)

    # With passing check and user_approved=True -> commits locally, never pushes
    done = execute_commit(
        tmp_path,
        cfg,
        "fix: update x to 2",
        project_profile="dev",
        files=["app.py"],
        verifier=lambda _: (True, "all green"),
        user_approved=True,
    )
    assert done["ok"] is True and done["commit"] and done["pushed"] is False

    pr = prepare_draft_pr(tmp_path, cfg, title="Update x", body="Clean change", project_profile="dev")
    assert pr["draft"] is True and pr["pushed"] is False and pr["published"] is False
