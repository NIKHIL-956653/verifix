"""Tasks through the HTTP API, with the background manager running a scripted model."""

import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import make_demo_repo
from tests.test_agent_loop import REGRESSION_TEST
from verifix.agent.loop import AgentLoop
from verifix.agent.manager import TaskManager
from verifix.agent.store import TaskStore
from verifix.core.config import Settings
from verifix.llm.fake import FakeLLM, tool_response
from verifix.sandbox.fake import FakeSandbox, failed, passed

pytestmark = pytest.mark.db


def call(name: str, args: dict[str, Any]) -> Any:
    return tool_response(name, args, call_id=f"toolu_{uuid.uuid4().hex[:10]}")


FIX_SCRIPT = [
    call(
        "create_file",
        {"path": "tests/test_missing_email.py", "content": REGRESSION_TEST, "reason": "regression"},
    ),
    call("run_tests", {"targets": ["tests/test_missing_email.py"], "purpose": "reproduce"}),
    call(
        "edit_file",
        {
            "path": "app/auth.py",
            "old_text": '    email = normalise_email(payload["email"])',
            "new_text": '    if not payload.get("email"):\n        raise LoginError("email required")\n    email = normalise_email(payload["email"])',
            "reason": "explicit check",
        },
    ),
    call("run_tests", {"targets": [], "purpose": "full suite after fix"}),
    call(
        "finish",
        {
            "outcome": "fixed",
            "summary": "fixed",
            "claims": [
                {"statement": "reproduced", "evidence": [2]},
                {"statement": "suite passes", "evidence": [4]},
            ],
            "uncertainty": "none",
        },
    ),
]


@pytest.fixture
async def env(api, db_sessionmaker, tmp_path: Path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VERIFIX_WORKSPACES_DIR", str(tmp_path / "ws"))
    settings = Settings(_env_file=None)
    store = TaskStore(db_sessionmaker)
    state: dict[str, Any] = {"llm": None, "sandbox": FakeSandbox()}
    manager = TaskManager(
        store,
        lambda: AgentLoop(
            store=store, llm=state["llm"], sandbox=state["sandbox"], settings=settings
        ),
    )
    api.app.state.manager = manager
    repo = make_demo_repo(tmp_path)
    rid = (await api.post("/repos", json={"path": str(repo)})).json()["id"]
    yield api, manager, state, rid, repo
    await manager.shutdown()


async def test_autonomous_fix_then_diff_patch_and_apply(env) -> None:  # type: ignore[no-untyped-def]
    api, manager, state, rid, repo = env
    state["llm"] = FakeLLM(list(FIX_SCRIPT))
    state["sandbox"] = FakeSandbox([failed(), passed(2)])
    r = await api.post(
        "/tasks",
        json={
            "repo_id": rid,
            "kind": "fix",
            "request": "500 on missing email",
            "policy": {"approve_plan": False, "write": "auto", "execute": "auto", "install": "ask"},
        },
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    await manager.wait(uuid.UUID(tid), limit_s=30)

    t = (await api.get(f"/tasks/{tid}")).json()
    assert t["status"] == "verified", t
    assert t["report"]["evidence_coverage"] == 1.0

    steps = (await api.get(f"/tasks/{tid}/steps")).json()
    assert steps[0]["kind"] == "status" and any(s["kind"] == "model_call" for s in steps)
    later = (await api.get(f"/tasks/{tid}/steps", params={"after": steps[-2]["seq"]})).json()
    assert len(later) == 1

    ev = (await api.get(f"/tasks/{tid}/evidence")).json()
    assert [e["kind"] for e in ev] == ["edit", "test_run", "edit", "test_run"]
    one = (await api.get(f"/tasks/{tid}/evidence/3")).json()
    assert 'raise LoginError("email required")' in one["content"]

    d = (await api.get(f"/tasks/{tid}/diff")).json()
    assert d["stats"]["files"] == 2 and "app/auth.py" in d["files"]
    patch = await api.get(f"/tasks/{tid}/patch")
    assert (
        patch.headers["content-type"].startswith("text/x-diff")
        and "+++ b/app/auth.py" in patch.text
    )

    assert (await api.post(f"/tasks/{tid}/apply", json={"confirm": False})).status_code == 422
    applied = (await api.post(f"/tasks/{tid}/apply", json={"confirm": True})).json()
    assert applied["applied"], applied
    assert 'raise LoginError("email required")' in (repo / "app/auth.py").read_text()
    assert (repo / "tests/test_missing_email.py").exists()


async def test_approvals_over_http(env) -> None:  # type: ignore[no-untyped-def]
    api, manager, state, rid, _ = env
    state["llm"] = FakeLLM(list(FIX_SCRIPT))
    state["sandbox"] = FakeSandbox([failed(), passed(2)])
    tid = (
        await api.post(
            "/tasks",
            json={
                "repo_id": rid,
                "kind": "fix",
                "request": "500 on missing email",
                "policy": {
                    "approve_plan": False,
                    "write": "ask",
                    "execute": "auto",
                    "install": "ask",
                },
            },
        )
    ).json()["id"]

    for expected in ("create_file", "edit_file"):
        await manager.wait(uuid.UUID(tid), limit_s=30)
        t = (await api.get(f"/tasks/{tid}")).json()
        assert t["status"] == "awaiting_approval"
        pa = t["pending_approval"]
        assert pa["action"] == expected and pa["preview"].startswith("---")
        r = await api.post(f"/tasks/{tid}/approvals/{pa['id']}", json={"approve": True})
        assert r.status_code == 200
        again = await api.post(f"/tasks/{tid}/approvals/{pa['id']}", json={"approve": True})
        assert again.status_code == 409  # a decision is final

    await manager.wait(uuid.UUID(tid), limit_s=30)
    assert (await api.get(f"/tasks/{tid}")).json()["status"] == "verified"


async def test_cancel_and_retry(env) -> None:  # type: ignore[no-untyped-def]
    api, manager, state, rid, _ = env
    state["llm"] = FakeLLM([call("create_file", {"path": "x.py", "content": "x\n", "reason": "r"})])
    tid = (
        await api.post(
            "/tasks",
            json={
                "repo_id": rid,
                "kind": "fix",
                "request": "do x",
                "policy": {"approve_plan": False},
            },
        )
    ).json()["id"]
    await manager.wait(uuid.UUID(tid), limit_s=30)
    r = await api.post(f"/tasks/{tid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await api.post(f"/tasks/{tid}/cancel")).status_code == 409

    # a failed task (here: the model ran out of script mid-run) can be retried with the saved conversation
    state["llm"] = FakeLLM([call("list_repository_files", {})])
    tid2 = (
        await api.post("/tasks", json={"repo_id": rid, "kind": "ask", "request": "list"})
    ).json()["id"]
    await manager.wait(uuid.UUID(tid2), limit_s=30)
    t2 = (await api.get(f"/tasks/{tid2}")).json()
    assert t2["status"] == "failed" and "FakeLLMExhausted" in t2["error"]
    state["llm"] = FakeLLM(
        [
            call(
                "finish",
                {
                    "outcome": "answered",
                    "summary": "s",
                    "claims": [{"statement": "listed", "evidence": [1]}],
                    "uncertainty": "u",
                },
            )
        ]
    )
    assert (await api.post(f"/tasks/{tid2}/retry")).status_code == 200
    await manager.wait(uuid.UUID(tid2), limit_s=30)
    t2 = (await api.get(f"/tasks/{tid2}")).json()
    assert t2["status"] == "completed" and t2["report"]["evidence_coverage"] == 1.0


async def test_create_task_validation(env) -> None:  # type: ignore[no-untyped-def]
    api, _, _, rid, _ = env
    assert (
        await api.post("/tasks", json={"repo_id": rid, "kind": "deploy", "request": "x x x"})
    ).status_code == 422
    missing = str(uuid.uuid4())
    assert (
        await api.post("/tasks", json={"repo_id": missing, "kind": "ask", "request": "x x x"})
    ).status_code == 404
    listed = (await api.get("/tasks", params={"repo_id": rid})).json()
    assert listed == []
