"""M1: the repository explorer, with emphasis on what it must refuse."""

import hashlib
import subprocess
from pathlib import Path

import pytest

from tests.fixtures import git, make_demo_repo
from verifix.repos import detect, files, gitinfo, search, symbols
from verifix.repos.gitcli import UnsafeRepoConfig, audit_config, ensure_safe
from verifix.repos.paths import PathRejected, RepoJail, deny_reason


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_demo_repo(tmp_path)


@pytest.fixture
def jail(repo: Path) -> RepoJail:
    return RepoJail(repo)


# ---------------------------------------------------------------- the jail


@pytest.mark.parametrize(
    "path,reason",
    [
        ("../outside.txt", "'..'"),
        ("app/../../outside.txt", "'..'"),
        ("/etc/passwd", "absolute"),
        ("C:/Windows/win.ini", "absolute"),
        ("C:\\Windows\\win.ini", "absolute"),
        (".env", "secret file"),
        ("deploy/server.pem", "secret file"),
        (".git/config", "protected directory"),
        ("escape.txt", "outside the repository"),
        ("innocent.txt", "resolves to a secret file"),
        ("", "empty"),
        ("a\x00b", "NUL"),
    ],
)
def test_jail_rejects(jail: RepoJail, path: str, reason: str) -> None:
    with pytest.raises(PathRejected) as info:
        jail.resolve(path)
    assert reason in info.value.reason


@pytest.mark.parametrize("path", ["app/auth.py", "./app/auth.py", "app\\auth.py", ".env.example"])
def test_jail_accepts(jail: RepoJail, path: str) -> None:
    real, rel = jail.resolve(path)
    assert real.is_file()
    assert "/" not in rel or rel.startswith(("app/", ".env"))


@pytest.mark.parametrize(
    "rel,denied",
    [
        (".env.local", True),
        ("config/.env.production", True),
        ("id_ed25519", True),
        ("home/.ssh/known_hosts", True),
        ("x/.aws/config", True),
        ("terraform.tfstate", True),
        (".npmrc", True),
        ("src/env.py", False),
        ("docs/keys.md", False),
        (".env.example", False),
    ],
)
def test_deny_list(rel: str, denied: bool) -> None:
    assert (deny_reason(rel) is not None) is denied


# ---------------------------------------------------------------- listing and reading


def test_list_respects_gitignore_and_hides_secrets(jail: RepoJail) -> None:
    listing = files.list_files(jail)
    paths = {f.path for f in listing.files}
    assert {"app/auth.py", "tests/test_auth.py", "web/ui.js", ".env.example", "README.md"} <= paths
    assert ".env" not in paths
    assert "deploy/server.pem" not in paths
    assert not any(p.startswith("node_modules/") for p in paths)
    assert "debug.log" not in paths
    assert "escape.txt" not in paths and "innocent.txt" not in paths
    assert listing.hidden >= 4  # .env, server.pem, two symlinks


def test_read_numbered_range(jail: RepoJail) -> None:
    r = files.read_file(jail, "app/auth.py", 12, 14)
    assert r.start == 12 and r.end == 14
    assert r.content.splitlines()[0].startswith("12 | def login")
    assert r.total_lines == 18
    full = (jail.root / "app/auth.py").read_bytes()
    assert r.sha256 == hashlib.sha256(full).hexdigest()


def test_read_caps_lines(jail: RepoJail) -> None:
    (jail.root / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(1000)))
    r = files.read_file(jail, "big.py", max_lines=50)
    assert r.end == 50 and r.truncated


def test_read_refuses_binary_ignored_and_missing(jail: RepoJail) -> None:
    with pytest.raises(files.NotReadable, match="binary"):
        files.read_file(jail, "logo.bin")
    with pytest.raises(files.NotReadable, match="ignored"):
        files.read_file(jail, "debug.log")
    with pytest.raises(files.NotReadable, match="no such file"):
        files.read_file(jail, "app/nope.py")
    with pytest.raises(files.NotReadable, match="past the end"):
        files.read_file(jail, "app/auth.py", 500)


def test_read_refuses_oversized(jail: RepoJail) -> None:
    (jail.root / "huge.txt").write_text("a" * (files.MAX_FILE_BYTES + 1))
    with pytest.raises(files.NotReadable, match="too large"):
        files.read_file(jail, "huge.txt")


# ---------------------------------------------------------------- search and symbols


def test_search_exact_finds_line_and_skips_secrets(jail: RepoJail) -> None:
    res = search.search(jail, 'payload["email"]')
    assert [(h.path, h.line) for h in res.hits] == [("app/auth.py", 14)]
    leaked = search.search(jail, "sk-ant-api03")
    assert leaked.hits == []  # the .env holds it, and the .env is never scanned


def test_search_regex_and_glob(jail: RepoJail) -> None:
    res = search.search(jail, r"def \w+\(", regex=True, path_glob="app/*")
    assert {h.line for h in res.hits} == {8, 12}
    with pytest.raises(search.BadPattern):
        search.search(jail, "([unclosed", regex=True)


def test_symbols_python_and_js(jail: RepoJail) -> None:
    syms = symbols.index_symbols(jail)
    by = {(s.path, s.qualname): s for s in syms}
    login = by[("app/auth.py", "login")]
    assert (login.kind, login.start, login.end) == ("function", 12, 18)
    assert by[("app/auth.py", "LoginError")].kind == "class"
    assert ("web/ui.js", "formatUser") in by and ("web/ui.js", "greet") in by
    assert ("web/ui.js", "Widget") in by
    assert symbols.find_symbol(jail, "login")[0].qualname == "login"


# ---------------------------------------------------------------- git, read-only


def _tree_fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def test_status_and_diff_are_read_only_on_a_dirty_tree(jail: RepoJail) -> None:
    root = jail.root
    (root / "app" / "auth.py").write_text((root / "app" / "auth.py").read_text() + "\n# wip\n")
    (root / "notes.txt").write_text("untracked\n")
    git(root, "add", "notes.txt")
    (root / ".env").write_text("CHANGED=1\n")  # a secret change must not show up anywhere

    before = _tree_fingerprint(root)
    st = gitinfo.status(jail)
    d = gitinfo.diff(jail)
    staged = gitinfo.diff(jail, staged=True)
    after = _tree_fingerprint(root)

    assert before == after
    paths = {e.path for e in st.entries}
    assert {"app/auth.py", "notes.txt"} <= paths
    assert ".env" not in paths and st.hidden >= 1
    assert st.branch == "main" and st.dirty
    assert "+# wip" in d.text and "CHANGED" not in d.text and d.hidden == 1
    assert staged.files == ["notes.txt"]


def test_diff_rejects_option_injection_as_base(jail: RepoJail) -> None:
    with pytest.raises(ValueError):
        gitinfo.diff(jail, base="--output=/tmp/pwned")


# ---------------------------------------------------------------- hostile git config


def test_filter_driver_in_repo_config_is_refused(repo: Path) -> None:
    assert audit_config(repo) == []
    git(repo, "config", "filter.evil.clean", "touch /tmp/verifix-pwned")
    with pytest.raises(UnsafeRepoConfig) as info:
        ensure_safe(repo)
    assert "filter.evil.clean" in info.value.keys


def test_fsmonitor_hook_never_runs(tmp_path: Path) -> None:
    repo = make_demo_repo(tmp_path, secrets=False, symlinks=False)
    marker = tmp_path / "fsmonitor-ran"
    git(repo, "config", "core.fsmonitor", f"touch {marker}")
    gitinfo.status(RepoJail(repo))
    assert not marker.exists()


# ---------------------------------------------------------------- overview


def test_overview(jail: RepoJail) -> None:
    ov = detect.overview(jail)
    assert ov.languages["Python"] >= 4
    assert "FastAPI" in ov.frameworks and "pytest" in ov.frameworks
    assert ov.test_runner == "pytest"
    assert "pyproject.toml" in ov.manifests
    assert ov.test_dirs == ["tests"]


def test_git_is_available() -> None:
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0
