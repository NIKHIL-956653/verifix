"""
Turning the model's `finish` call into the task's final report and status.

The model *claims* an outcome; this module decides what the evidence supports. Rules:

- Every claim must cite evidence numbers that exist in this task. Claims citing nothing,
  or citing evidence that does not exist, are kept but marked unsupported, and
  `evidence_coverage` = supported / total claims.
- "reproduced" needs at least one recorded failing test run. Otherwise it becomes
  "root_cause_suspected".
- "fixed" needs at least one changed file. It becomes status `verified` only if the
  last test run recorded AFTER the last change exited 0. Otherwise the status is
  `fixed_unverified` and the report says why.
- An outcome that does not fit the task kind (e.g. "fixed" on a read-only question) is
  replaced by the kind's neutral outcome, with a note.

Pure function: the inputs are plain data, so the rules are unit-tested directly.
"""

import re
from dataclasses import dataclass, field
from typing import Any

_ALLOWED = {
    "ask": {"answered", "blocked"},
    "investigate": {"root_cause_suspected", "reproduced", "no_fix_needed", "blocked"},
    "fix": {"root_cause_suspected", "reproduced", "fixed", "no_fix_needed", "blocked"},
    "review": {"review_complete", "blocked"},
}
_DEFAULT = {
    "ask": "answered",
    "investigate": "root_cause_suspected",
    "fix": "root_cause_suspected",
    "review": "review_complete",
}
_TEST_FILE = re.compile(
    r"(^|/)(tests?/|test_[^/]+\.py$|[^/]+_test\.py$|[^/]+\.(test|spec)\.[jt]sx?$)"
)


@dataclass
class EvidenceRef:
    seq: int
    kind: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaimIn:
    statement: str
    evidence: list[int]


@dataclass
class FinishData:
    outcome: str
    summary: str
    root_cause: str | None
    claims: list[ClaimIn]
    uncertainty: str
    next_steps: list[str]


def reconcile(
    kind: str, data: FinishData, evidence: list[EvidenceRef], changed_files: list[str]
) -> tuple[str, dict[str, Any]]:
    notes: list[str] = []
    outcome = data.outcome
    if outcome not in _ALLOWED[kind]:
        notes.append(
            f"outcome '{outcome}' does not apply to a {kind} task; recorded as '{_DEFAULT[kind]}'"
        )
        outcome = _DEFAULT[kind]

    valid = {e.seq for e in evidence}
    changes = [e for e in evidence if e.kind == "edit"]
    last_change = max((e.seq for e in changes), default=0)
    runs = [e for e in evidence if e.kind == "test_run"]
    failing = [r for r in runs if r.meta.get("status") == "failed"]
    after = [r for r in runs if r.seq > last_change]
    last_after = after[-1] if after else None

    if outcome == "reproduced" and not failing:
        notes.append("no failing test run was recorded, so the failure was not reproduced")
        outcome = "root_cause_suspected"

    status = "completed"
    if outcome == "fixed":
        if not changed_files:
            notes.append("outcome 'fixed' was claimed but no file was changed")
            outcome = "root_cause_suspected"
        elif last_after is None:
            status = "fixed_unverified"
            notes.append("no test run was recorded after the last change")
        elif last_after.meta.get("status") != "passed":
            status = "fixed_unverified"
            notes.append(
                f"the last test run after the last change was '{last_after.meta.get('status')}' "
                f"(E{last_after.seq}), not 'passed'"
            )
        else:
            status = "verified"

    claims = []
    for c in data.claims:
        missing = [i for i in c.evidence if i not in valid]
        supported = bool(c.evidence) and not missing
        claims.append(
            {
                "statement": c.statement,
                "evidence": c.evidence,
                "supported": supported,
                "missing": missing,
            }
        )
    coverage = round(sum(c["supported"] for c in claims) / len(claims), 3) if claims else 0.0
    if not claims:
        notes.append("the report made no claims backed by evidence")

    # Writing the regression test is a change too; "before the fix" means before the first
    # change to a non-test file.
    source_changes = [e for e in changes if not _TEST_FILE.search(str(e.meta.get("path", "")))]
    first_fix = source_changes[0].seq if source_changes else 10**9
    reproduced_before_fix = any(r.seq < first_fix for r in failing)
    report = {
        "outcome": outcome,
        "claimed_outcome": data.outcome,
        "summary": data.summary,
        "root_cause": data.root_cause,
        "claims": claims,
        "evidence_coverage": coverage,
        "uncertainty": data.uncertainty,
        "next_steps": data.next_steps,
        "notes": notes,
        "changed_files": changed_files,
        "regression_test_changed": any(_TEST_FILE.search(p) for p in changed_files),
        "verification": {
            "test_runs": len(runs),
            "failing_runs": len(failing),
            "reproduced_before_fix": reproduced_before_fix,
            "last_run_after_last_change": (
                {
                    "evidence": last_after.seq,
                    **{
                        k: last_after.meta.get(k)
                        for k in ("status", "exit_code", "passed", "failed", "targets")
                    },
                }
                if last_after
                else None
            ),
        },
    }
    return status, report
