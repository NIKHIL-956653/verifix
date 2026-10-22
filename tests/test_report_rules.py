"""The reconciliation rules, directly: what the evidence lets an outcome claim."""

from verifix.agent.report import ClaimIn, EvidenceRef, FinishData, reconcile


def fin(outcome: str, claims: list[ClaimIn] | None = None) -> FinishData:
    return FinishData(outcome, "s", None, claims or [], "u", [])


def run(seq: int, status: str) -> EvidenceRef:
    return EvidenceRef(
        seq, "test_run", {"status": status, "exit_code": 0 if status == "passed" else 1}
    )


def edit(seq: int, path: str = "app/x.py") -> EvidenceRef:
    return EvidenceRef(seq, "edit", {"path": path, "op": "edit"})


def test_fixed_and_passing_after_last_change_is_verified() -> None:
    status, r = reconcile("fix", fin("fixed"), [edit(1), run(2, "passed")], ["app/x.py"])
    assert status == "verified" and r["outcome"] == "fixed"


def test_passing_run_before_a_later_change_does_not_count() -> None:
    status, r = reconcile("fix", fin("fixed"), [edit(1), run(2, "passed"), edit(3)], ["app/x.py"])
    assert status == "fixed_unverified"
    assert "no test run was recorded after the last change" in r["notes"]


def test_blocked_run_after_change_is_unverified() -> None:
    status, _ = reconcile("fix", fin("fixed"), [edit(1), run(2, "blocked")], ["app/x.py"])
    assert status == "fixed_unverified"


def test_fixed_without_changes_is_downgraded() -> None:
    status, r = reconcile("fix", fin("fixed"), [run(1, "passed")], [])
    assert status == "completed" and r["outcome"] == "root_cause_suspected"


def test_reproduced_needs_a_failing_run() -> None:
    _, r = reconcile("investigate", fin("reproduced"), [run(1, "passed")], [])
    assert r["outcome"] == "root_cause_suspected"
    _, r = reconcile("investigate", fin("reproduced"), [run(1, "failed")], [])
    assert r["outcome"] == "reproduced"


def test_reproduced_before_fix_ignores_test_file_edits() -> None:
    ev = [edit(1, "tests/test_x.py"), run(2, "failed"), edit(3, "app/x.py"), run(4, "passed")]
    _, r = reconcile("fix", fin("fixed"), ev, ["app/x.py", "tests/test_x.py"])
    assert r["verification"]["reproduced_before_fix"] is True
    assert r["regression_test_changed"] is True


def test_outcome_must_fit_the_kind() -> None:
    _, r = reconcile("ask", fin("fixed"), [], [])
    assert r["outcome"] == "answered" and r["claimed_outcome"] == "fixed"
    _, r = reconcile("review", fin("answered"), [], [])
    assert r["outcome"] == "review_complete"


def test_coverage_counts_only_fully_supported_claims() -> None:
    claims = [ClaimIn("a", [1]), ClaimIn("b", [1, 9]), ClaimIn("c", [])]
    _, r = reconcile("ask", fin("answered", claims), [EvidenceRef(1, "file_read")], [])
    assert r["evidence_coverage"] == round(1 / 3, 3)
    assert [c["supported"] for c in r["claims"]] == [True, False, False]
