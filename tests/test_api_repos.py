"""M1 acceptance through the HTTP API, against the real test database."""

from pathlib import Path

import pytest

from tests.fixtures import make_demo_repo

pytestmark = pytest.mark.db


async def _register(api, path: Path) -> str:  # type: ignore[no-untyped-def]
    r = await api.post("/repos", json={"path": str(path)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_register_is_idempotent_and_listed(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo = make_demo_repo(tmp_path)
    a = await _register(api, repo / "app")  # a subdirectory resolves to the repo root
    b = await _register(api, repo)
    assert a == b
    listed = (await api.get("/repos")).json()
    assert [r["id"] for r in listed] == [a]
    assert listed[0]["root_path"] == str(repo.resolve())
    assert listed[0]["default_branch"] == "main"


@pytest.mark.parametrize("bad", ["", "/definitely/not/here"])
async def test_register_rejects_bad_paths(api, bad: str) -> None:  # type: ignore[no-untyped-def]
    r = await api.post("/repos", json={"path": bad})
    assert r.status_code == 422


async def test_register_rejects_non_git_dir(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    r = await api.post("/repos", json={"path": str(tmp_path)})
    assert r.status_code == 422
    assert "not a git repository" in r.text


async def test_allowed_roots_are_enforced(api, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from verifix.core.config import Settings, get_settings

    repo = make_demo_repo(tmp_path)
    monkeypatch.setenv("VERIFIX_ALLOWED_ROOTS", str(tmp_path / "somewhere-else"))
    api.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    r = await api.post("/repos", json={"path": str(repo)})
    assert r.status_code == 422 and "ALLOWED_ROOTS" in r.text


async def test_explorer_endpoints(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo = make_demo_repo(tmp_path)
    rid = await _register(api, repo)

    listing = (await api.get(f"/repos/{rid}/files")).json()
    paths = {f["path"] for f in listing["files"]}
    assert "app/auth.py" in paths and ".env" not in paths

    f = (
        await api.get(f"/repos/{rid}/file", params={"path": "app/auth.py", "start": 12, "end": 14})
    ).json()
    assert f["content"].startswith("12 | def login")

    for path, code in [
        (".env", 403),
        ("../outside.txt", 403),
        ("innocent.txt", 403),
        ("logo.bin", 422),
    ]:
        r = await api.get(f"/repos/{rid}/file", params={"path": path})
        assert r.status_code == code, (path, r.text)
        assert "REALSECRET" not in r.text

    hits = (await api.get(f"/repos/{rid}/search", params={"q": "normalise_email"})).json()["hits"]
    assert {(h["path"], h["line"]) for h in hits} == {("app/auth.py", 8), ("app/auth.py", 14)}
    assert (
        await api.get(f"/repos/{rid}/search", params={"q": "(", "regex": True})
    ).status_code == 422

    syms = (await api.get(f"/repos/{rid}/symbols", params={"name": "login"})).json()
    assert syms[0]["start"] == 12

    ov = (await api.get(f"/repos/{rid}/overview")).json()
    assert ov["test_runner"] == "pytest"

    (repo / "app" / "auth.py").write_text("changed\n")
    st = (await api.get(f"/repos/{rid}/git/status")).json()
    assert st["dirty"] and any(e["path"] == "app/auth.py" for e in st["entries"])
    d = (await api.get(f"/repos/{rid}/git/diff")).json()
    assert "+changed" in d["text"]


async def test_unknown_repo_is_404(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.get("/repos/00000000-0000-0000-0000-000000000000/files")
    assert r.status_code == 404


async def test_repo_whose_config_turns_hostile_is_refused(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from tests.fixtures import git

    repo = make_demo_repo(tmp_path)
    rid = await _register(api, repo)
    git(repo, "config", "filter.x.smudge", "evil")
    r = await api.get(f"/repos/{rid}/files")
    assert r.status_code == 409
