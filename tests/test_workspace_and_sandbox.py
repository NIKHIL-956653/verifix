"""
M4 workspaces and M5 sandbox.

The sandbox tests use real Docker and skip cleanly where it is unavailable. They need a
base image with python + pytest: `VERIFIX_TEST_SANDBOX_IMAGE` (default python:3.12-slim
with pytest installed on first use is NOT attempted - the run must stay offline).
"""

import hashlib
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.fixtures import git, make_demo_repo
from verifix.agent import workspace
from verifix.sandbox.base import InvalidTestArgs, validate_keyword, validate_target
from verifix.sandbox.docker import DockerSandbox, _has_python_dependencies


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts or p.is_symlink() or not p.is_file():
            continue
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_demo_repo(tmp_path, symlinks=False)


@pytest.fixture
def wsdir(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


# ------------------------------------------------------------------ workspaces


def test_workspace_snapshots_uncommitted_work_without_touching_the_checkout(
    repo: Path, wsdir: Path
) -> None:
    (repo / "app" / "auth.py").write_text((repo / "app" / "auth.py").read_text() + "# user wip\n")
    (repo / "notes.md").write_text("untracked note\n")
    (repo / ".env.local").write_text("SECRET=1\n")  # untracked secret: must not be copied
    before = _fingerprint(repo)

    ws = workspace.create(repo, uuid.uuid4(), wsdir)

    assert _fingerprint(repo) == before
    assert ws.path.is_dir() and not ws.path.is_relative_to(repo)
    assert (ws.path / "app" / "auth.py").read_text().endswith("# user wip\n")
    assert (ws.path / "notes.md").exists()
    assert not (ws.path / ".env.local").exists()
    assert workspace.changed_files(ws.path, ws.baseline) == []  # baseline includes the wip
    assert ws.branch.startswith("verifix/")


def test_agent_changes_diff_revert_and_remove(repo: Path, wsdir: Path) -> None:
    ws = workspace.create(repo, uuid.uuid4(), wsdir)
    (ws.path / "app" / "auth.py").write_text("print('changed')\n")
    (ws.path / "tests" / "test_new.py").write_text("def test_x():\n    assert True\n")
    assert set(workspace.changed_files(ws.path, ws.baseline)) == {
        "app/auth.py",
        "tests/test_new.py",
    }
    d = workspace.diff(ws.path, ws.baseline)
    assert "+print('changed')" in d and "tests/test_new.py" in d
    assert workspace.diff_stats(ws.path, ws.baseline)["files"] == 2

    assert workspace.revert(ws.path, ws.baseline, "app/auth.py") == "restored"
    assert workspace.revert(ws.path, ws.baseline, "tests/test_new.py") == "deleted"
    assert workspace.changed_files(ws.path, ws.baseline) == []

    workspace.remove(repo, ws.path, ws.branch)
    assert not ws.path.exists()
    branches = git(repo, "branch", "--list", "verifix/*")
    assert branches.strip() == ""


def test_apply_to_source_succeeds_on_clean_files(repo: Path, wsdir: Path) -> None:
    ws = workspace.create(repo, uuid.uuid4(), wsdir)
    target = ws.path / "app" / "auth.py"
    target.write_text(target.read_text().replace('payload["email"]', 'payload.get("email", "")'))
    res = workspace.apply_to_source(ws.path, ws.baseline, repo)
    assert res.applied, res.message
    assert 'payload.get("email", "")' in (repo / "app" / "auth.py").read_text()
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""  # nothing staged


def test_apply_to_source_refuses_when_user_changed_the_same_file(repo: Path, wsdir: Path) -> None:
    ws = workspace.create(repo, uuid.uuid4(), wsdir)
    (ws.path / "app" / "auth.py").write_text("agent version\n")
    (repo / "app" / "auth.py").write_text("user edited meanwhile\n")
    res = workspace.apply_to_source(ws.path, ws.baseline, repo)
    assert not res.applied and res.conflicts == ["app/auth.py"]
    assert (repo / "app" / "auth.py").read_text() == "user edited meanwhile\n"


def test_post_checkout_hook_does_not_run(repo: Path, wsdir: Path, tmp_path: Path) -> None:
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    workspace.create(repo, uuid.uuid4(), wsdir)
    assert not marker.exists()


def test_workspace_requires_a_commit(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    with pytest.raises(workspace.WorkspaceError, match="no commits"):
        workspace.create(empty, uuid.uuid4(), tmp_path / "ws")


# ------------------------------------------------------------------ test argument validation


@pytest.mark.parametrize(
    "target",
    [
        "tests/test_auth.py",
        "tests/test_auth.py::test_login_ok",
        "tests/test_a.py::TestX::test_y[1-2]",
    ],
)
def test_valid_targets(target: str) -> None:
    assert validate_target(target) == target


@pytest.mark.parametrize(
    "target", ["-x", "--rootdir=/", "/etc/passwd", "../x.py", "tests/a.py; rm -rf /", "$(id)", ""]
)
def test_invalid_targets(target: str) -> None:
    with pytest.raises(InvalidTestArgs):
        validate_target(target)


def test_keyword_validation() -> None:
    assert validate_keyword("login and not slow") == "login and not slow"
    with pytest.raises(InvalidTestArgs):
        validate_keyword("x; rm -rf /")


def test_dependency_detection(tmp_path: Path) -> None:
    assert not _has_python_dependencies(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=[]\n[project.optional-dependencies]\ntest=["pytest"]\n'
    )
    assert not _has_python_dependencies(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["fastapi"]\n')
    assert _has_python_dependencies(tmp_path)


def test_pytest_argv_has_every_isolation_flag(tmp_path: Path) -> None:
    sb = DockerSandbox(memory="512m", cpus="1", pids=64)
    argv = sb.pytest_argv(tmp_path, "img", targets=["tests/test_a.py"], keyword="login", name="n")
    joined = " ".join(argv)
    for flag in [
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--user 1000:1000",
        "--memory 512m",
        "--pids-limit 64",
        f"{tmp_path}:/work:ro",
    ]:
        assert flag in joined, flag
    assert argv[-2:] == ["--", "tests/test_a.py"]
    assert "-k" in argv and "login" in argv


# ------------------------------------------------------------------ real docker

SANDBOX_IMAGE = os.environ.get("VERIFIX_TEST_SANDBOX_IMAGE", "verifix-base:py312")


def _docker_ready() -> str | None:
    sb = DockerSandbox(base_image=SANDBOX_IMAGE)
    ok, why = sb.available()
    if not ok:
        return why
    if (
        subprocess.run(
            ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True
        ).returncode
        != 0
    ):
        return f"sandbox image {SANDBOX_IMAGE} not present"
    return None


docker = pytest.mark.skipif(
    _docker_ready() is not None, reason=f"docker sandbox unavailable: {_docker_ready()}"
)


def _project(tmp_path: Path, tests: str) -> Path:
    p = tmp_path / "proj"
    (p / "tests").mkdir(parents=True)
    (p / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (p / "tests" / "test_calc.py").write_text(tests)
    for d in (p, p / "tests"):
        d.chmod(0o755)
    return p


@docker
async def test_real_sandbox_pass_fail_and_counts(tmp_path: Path) -> None:
    proj = _project(
        tmp_path,
        "from calc import add\n\ndef test_ok():\n    assert add(1, 2) == 3\n\n"
        "def test_bad():\n    assert add(1, 2) == 4\n",
    )
    sb = DockerSandbox(base_image=SANDBOX_IMAGE, timeout_s=120)
    env = await sb.ensure_environment(proj, allow_install=False)
    assert env.ok and env.image == SANDBOX_IMAGE and not env.built

    both = await sb.run_pytest(
        proj, targets=[], keyword=None, image=env.image, run_id=uuid.uuid4().hex[:8]
    )
    assert both.status == "failed" and both.exit_code == 1
    assert (both.passed, both.failed) == (1, 1)
    assert both.failing_tests == ["tests/test_calc.py::test_bad"]
    assert both.environment["network"] == "none"

    one = await sb.run_pytest(
        proj,
        targets=["tests/test_calc.py::test_ok"],
        keyword=None,
        image=env.image,
        run_id=uuid.uuid4().hex[:8],
    )
    assert one.status == "passed" and one.exit_code == 0 and one.passed == 1

    none = await sb.run_pytest(
        proj,
        targets=[],
        keyword="nothing_matches_this",
        image=env.image,
        run_id=uuid.uuid4().hex[:8],
    )
    assert none.status == "no_tests" and none.exit_code == 5


@docker
async def test_real_sandbox_blocks_network_writes_and_runaway_tests(tmp_path: Path) -> None:
    proj = _project(
        tmp_path,
        "import socket, pathlib, pytest\n\n"
        "def test_no_network():\n"
        "    s = socket.socket()\n"
        "    s.settimeout(3)\n"
        "    with pytest.raises(OSError):\n"
        "        s.connect(('1.1.1.1', 80))\n\n"
        "def test_repo_is_read_only():\n"
        "    with pytest.raises(OSError):\n"
        "        pathlib.Path('/work/pwned.txt').write_text('x')\n\n"
        "def test_tmp_is_writable():\n"
        "    pathlib.Path('/tmp/ok.txt').write_text('x')\n\n"
        "def test_not_root():\n"
        "    import os\n"
        "    assert os.getuid() == 1000\n",
    )
    sb = DockerSandbox(base_image=SANDBOX_IMAGE, timeout_s=120)
    res = await sb.run_pytest(
        proj, targets=[], keyword=None, image=SANDBOX_IMAGE, run_id=uuid.uuid4().hex[:8]
    )
    assert res.status == "passed", res.output_tail
    assert res.passed == 4
    assert not (proj / "pwned.txt").exists()

    (proj / "tests" / "test_calc.py").write_text(
        "def test_forever():\n    while True:\n        pass\n"
    )
    slow = DockerSandbox(base_image=SANDBOX_IMAGE, timeout_s=5)
    hung = await slow.run_pytest(
        proj, targets=[], keyword=None, image=SANDBOX_IMAGE, run_id=uuid.uuid4().hex[:8]
    )
    assert hung.status == "timeout" and hung.exit_code is None
    assert "killed after 5s" in (hung.limitation or "")


async def test_sandbox_reports_blocked_without_docker(tmp_path: Path) -> None:
    sb = DockerSandbox(docker_bin="definitely-not-docker")
    res = await sb.run_pytest(tmp_path, targets=[], keyword=None, image=None, run_id="x")
    assert res.status == "blocked" and "not found" in (res.limitation or "")


@docker
async def test_runtime_image_required_when_base_lacks_pytest(tmp_path: Path) -> None:
    """A base image without pytest (like stock python:3.12-slim) is detected; the agent is
    told to build the runtime instead of every run failing with 'No module named pytest'."""
    bare = "verifix-test-nopytest:latest"
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        f"FROM {SANDBOX_IMAGE}\n"
        'RUN python -c "import pytest, _pytest, shutil, os; '
        '[shutil.rmtree(os.path.dirname(m.__file__)) for m in (pytest, _pytest)]"\n'
    )
    built = subprocess.run(["docker", "build", "-q", "-t", bare, str(ctx)], capture_output=True)
    if built.returncode != 0:
        pytest.skip(f"could not derive test image: {built.stderr.decode()[-200:]}")
    proj = _project(tmp_path, "def test_ok():\n    assert True\n")
    sb = DockerSandbox(base_image=bare, timeout_s=60)
    subprocess.run(["docker", "image", "rm", "-f", sb.runtime_tag], capture_output=True)
    env = await sb.ensure_environment(proj, allow_install=False)
    assert not env.ok and env.needs_install
    assert "setup-sandbox" in (env.limitation or "")
    res = await sb.run_pytest(proj, targets=[], keyword=None, image=None, run_id="rt")
    assert res.status == "blocked" and "setup-sandbox" in (res.limitation or "")

    ok = DockerSandbox(base_image=SANDBOX_IMAGE)
    env2 = await ok.ensure_environment(proj, allow_install=False)
    assert env2.ok and env2.image == SANDBOX_IMAGE


async def test_runtime_image_is_enough_without_the_base_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BuildKit (Docker Desktop) does not keep the FROM image as a local tag, so after
    setup-sandbox only verifix-runtime exists. That must be enough to run tests."""
    sb = DockerSandbox(base_image="python:3.12-slim")
    present = {sb.runtime_tag}
    monkeypatch.setattr(sb, "available", lambda: (True, "29"))
    monkeypatch.setattr(sb, "_image_exists", lambda tag: tag in present)
    env = await sb.ensure_environment(tmp_path, allow_install=False)
    assert env.ok and env.image == sb.runtime_tag

    present.clear()
    monkeypatch.setattr(sb, "_has_pytest", lambda image: False)
    env = await sb.ensure_environment(tmp_path, allow_install=False)
    assert not env.ok and "setup-sandbox" in (env.limitation or "")
