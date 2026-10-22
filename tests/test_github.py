"""
M8 GitHub integration, offline.

GitHub's REST API is a MockTransport; "origin" fetches from a github.com URL but pushes
to a local bare repository (remote.origin.pushurl), so the full PR flow - build the branch
from source_head + patch, push, open the PR - runs for real without network.
"""

import base64
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx2
import pytest

from tests.fixtures import git, make_demo_repo
from tests.test_api_tasks import FIX_SCRIPT
from verifix.agent.loop import AgentLoop
from verifix.agent.manager import TaskManager
from verifix.agent.store import TaskStore
from verifix.core.config import Settings, get_settings
from verifix.github import ops
from verifix.github.client import GitHubClient, GitHubError, git_auth_env, parse_repo_url
from verifix.llm.fake import FakeLLM
from verifix.sandbox.fake import FakeSandbox, failed, passed


def test_parse_repo_url() -> None:
    r = parse_repo_url("https://github.com/NIKHIL-956653/group1-daily.git")
    assert (r.owner, r.name) == ("NIKHIL-956653", "group1-daily")
    assert parse_repo_url("https://github.com/a/b/").full_name == "a/b"
    for bad in (
        "git@github.com:a/b.git",
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "https://token@github.com/a/b",
        "https://github.com/a/b/tree/main",
        "https://github.com/a/..",
        "file:///etc",
    ):
        with pytest.raises(GitHubError):
            parse_repo_url(bad)


def test_git_auth_env_scopes_token_to_github() -> None:
    assert git_auth_env(None) == {}
    env = git_auth_env("ghp_secret")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert "ghp_secret" not in json.dumps(env)  # base64'd, never in argv
    encoded = env["GIT_CONFIG_VALUE_0"].split()[-1]
    assert base64.b64decode(encoded).decode() == "x-access-token:ghp_secret"


async def test_client_errors_and_checks() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.headers["Authorization"] == "Bearer tok"
        if req.url.path.endswith("/check-runs"):
            return httpx2.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "ci",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "u",
                        }
                    ]
                },
            )
        return httpx2.Response(404, json={"message": "Not Found"})

    c = GitHubClient("tok", transport=httpx2.MockTransport(handler))
    ref = parse_repo_url("https://github.com/a/b")
    checks = await c.check_runs(ref, "abc")
    assert checks == [{"name": "ci", "status": "completed", "conclusion": "success", "url": "u"}]
    with pytest.raises(GitHubError) as e:
        await c.get_repo(ref)
    assert e.value.status == 404
    await c.aclose()


def test_build_branch_excludes_uncommitted_user_changes(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path, secrets=False, symlinks=False)
    head = git(repo, "rev-parse", "HEAD").strip()
    # The user has an unrelated uncommitted edit; it must not end up in the PR.
    (repo / "README.md").write_text("# demo\nLOCAL WORK IN PROGRESS\n")
    patch = (
        b"diff --git a/app/auth.py b/app/auth.py\n"
        b"--- a/app/auth.py\n+++ b/app/auth.py\n"
        b"@@ -1,3 +1,3 @@\n"
        b'-"""Login handling."""\n+"""Login handling (fixed)."""\n \n \n'
    )
    sha = ops.build_branch(repo, head, patch, "verifix/pr-test", "fix login")
    files = git(repo, "show", "--name-only", "--format=", sha).split()
    assert files == ["app/auth.py"]
    assert "LOCAL WORK" not in git(repo, "show", f"{sha}:README.md")
    assert "LOCAL WORK" in (repo / "README.md").read_text()  # checkout untouched
    assert "verifix-pr" not in git(repo, "worktree", "list")

    with pytest.raises(GitHubError, match="does not apply"):
        ops.build_branch(repo, head, b"diff --git a/x b/x\ngarbage\n", "verifix/pr-bad", "m")
    assert "verifix/pr-bad" not in git(repo, "branch", "--list")


def test_origin_detection(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path, secrets=False, symlinks=False)
    assert ops.origin(repo) is None
    git(repo, "remote", "add", "origin", "git@github.com:acme/demo.git")
    assert ops.origin(repo).full_name == "acme/demo"  # type: ignore[union-attr]
    git(repo, "remote", "set-url", "origin", "https://example.com/acme/demo.git")
    assert ops.origin(repo) is None


# ------------------------------------------------------------------ API


class FakeGitHub:
    def __init__(self) -> None:
        self.pulls: list[dict[str, Any]] = []

    def __call__(self, req: httpx2.Request) -> httpx2.Response:
        path = req.url.path
        if req.method == "GET" and path == "/repos/acme/demo":
            return httpx2.Response(200, json={"default_branch": "main"})
        if req.method == "POST" and path == "/repos/acme/demo/pulls":
            body = json.loads(req.content)
            self.pulls.append(body)
            return httpx2.Response(
                201, json={"number": 7, "html_url": "https://github.com/acme/demo/pull/7"}
            )
        if path.startswith("/repos/acme/demo/commits/") and path.endswith("/check-runs"):
            return httpx2.Response(
                200,
                json={"check_runs": [{"name": "ci", "status": "in_progress", "conclusion": None}]},
            )
        return httpx2.Response(404, json={"message": "Not Found"})


@pytest.fixture
async def gh_env(api, db_sessionmaker, tmp_path: Path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VERIFIX_WORKSPACES_DIR", str(tmp_path / "ws"))
    settings = Settings(
        _env_file=None, github_token="ghp_test", clones_dir=str(tmp_path / "clones")
    )
    api.app.dependency_overrides[get_settings] = lambda: settings
    store = TaskStore(db_sessionmaker)
    manager = TaskManager(
        store,
        lambda: AgentLoop(
            store=store,
            llm=FakeLLM(list(FIX_SCRIPT)),
            sandbox=FakeSandbox([failed(), passed(2)]),
            settings=settings,
        ),
    )
    api.app.state.manager = manager
    fake = FakeGitHub()
    api.app.state.github_transport = httpx2.MockTransport(fake)

    repo = make_demo_repo(tmp_path, secrets=False, symlinks=False)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", "https://github.com/acme/demo.git")
    git(repo, "config", "remote.origin.pushurl", str(bare))
    git(repo, "push", "-q", str(bare), "main")
    rid = (await api.post("/repos", json={"path": str(repo)})).json()["id"]
    yield api, manager, fake, rid, repo, bare, settings
    await manager.shutdown()


async def test_pull_request_flow(gh_env) -> None:  # type: ignore[no-untyped-def]
    api, manager, fake, rid, _repo, bare, _settings = gh_env
    tid = (
        await api.post(
            "/tasks",
            json={
                "repo_id": rid,
                "kind": "fix",
                "request": "500 on missing email",
                "policy": {"approve_plan": False, "write": "auto"},
            },
        )
    ).json()["id"]
    await manager.wait(uuid.UUID(tid), limit_s=30)
    assert (await api.get(f"/tasks/{tid}")).json()["status"] == "verified"

    r = await api.post(f"/tasks/{tid}/pull-request", json={})
    assert r.status_code == 422 and "confirm=true" in r.text
    assert git(bare, "branch", "--list").strip() == "main"  # nothing pushed without confirm

    r = await api.post(f"/tasks/{tid}/pull-request", json={"confirm": True})
    assert r.status_code == 200, r.text
    pr = r.json()
    assert pr["number"] == 7 and pr["branch"] == f"verifix/pr-{tid[:12]}" and pr["draft"]
    assert fake.pulls[0]["draft"] is True and fake.pulls[0]["base"] == "main"
    assert "E" in fake.pulls[0]["body"] and "verified" in fake.pulls[0]["body"]

    pushed = git(bare, "show", "--name-only", "--format=%s", pr["branch"]).split("\n")
    assert pushed[0].startswith("verifix:")
    assert set(filter(None, pushed[1:])) == {"app/auth.py", "tests/test_missing_email.py"}

    again = await api.post(f"/tasks/{tid}/pull-request", json={"confirm": True})
    assert again.status_code == 409

    checks = (await api.get(f"/tasks/{tid}/pull-request/checks")).json()
    assert checks["overall"] == "pending"

    t = (await api.get(f"/tasks/{tid}")).json()
    assert t["report"]["pull_request"]["number"] == 7
    steps = (await api.get(f"/tasks/{tid}/steps")).json()
    assert any(s["name"] == "pull_request" and "PR #7" in s["summary"] for s in steps)


async def test_pull_request_needs_token(gh_env) -> None:  # type: ignore[no-untyped-def]
    api, manager, _, rid, *_ = gh_env
    api.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    tid = (
        await api.post(
            "/tasks",
            json={
                "repo_id": rid,
                "kind": "fix",
                "request": "500 on missing email",
                "policy": {"approve_plan": False, "write": "auto"},
            },
        )
    ).json()["id"]
    await manager.wait(uuid.UUID(tid), limit_s=30)
    r = await api.post(f"/tasks/{tid}/pull-request", json={"confirm": True})
    assert r.status_code == 400 and "VERIFIX_GITHUB_TOKEN" in r.text
    assert (await api.get("/github/status")).json()["token_configured"] is False


async def test_import_by_url(gh_env, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    api, *_ = gh_env
    r = await api.post("/github/import", json={"url": "https://github.com/acme/other"})
    assert r.status_code == 422 and "confirm=true" in r.text
    assert (
        await api.post("/github/import", json={"url": "nope", "confirm": True})
    ).status_code == 422

    def fake_clone(ref, clones_dir, token):  # type: ignore[no-untyped-def]
        assert token == "ghp_test"
        dest = Path(clones_dir) / f"{ref.owner}__{ref.name}"
        dest.mkdir(parents=True)
        git(dest, "init", "-q", "-b", "main")
        (dest / "a.py").write_text("x = 1\n")
        git(dest, "add", "-A")
        git(dest, "commit", "-q", "-m", "init")
        return dest.resolve(), True

    monkeypatch.setattr(ops, "clone", fake_clone)
    r = await api.post(
        "/github/import", json={"url": "https://github.com/acme/other.git", "confirm": True}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cloned"] and body["repo"]["name"] == "acme__other"
    files = (await api.get(f"/repos/{body['repo']['id']}/files")).json()
    assert any(f["path"] == "a.py" for f in files["files"])
