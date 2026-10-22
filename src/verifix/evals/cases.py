"""
Benchmark cases: small repositories with one seeded bug each.

Layout of `cases/<id>/`:

    case.toml   id, title, category, request (what a user would report), expected_files
    repo/       the buggy project, including its visible tests (they pass on the bug)
    hidden/     tests the agent never sees; copied over the agent's result at grading
    fix/        the reference fix (files that replace their repo/ counterparts)

The reference fix exists to validate the ruler: grading it must score 1.0, and grading an
empty patch must score 0.0. If either fails, the benchmark is broken, not the agent.
"""

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from verifix.repos.gitcli import run_git

CASES_DIR = Path(__file__).parent / "cases"
HIDDEN_PREFIX = "tests/test_verifix_hidden"
_IDENTITY = ["user.name=verifix-bench", "user.email=bench@verifix.invalid"]


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    category: str
    request: str
    expected_files: list[str]
    root: Path = field(repr=False)

    @property
    def repo_dir(self) -> Path:
        return self.root / "repo"

    @property
    def hidden_dir(self) -> Path:
        return self.root / "hidden"

    @property
    def fix_dir(self) -> Path:
        return self.root / "fix"

    def hidden_files(self) -> list[str]:
        return _rel_files(self.hidden_dir)

    def public(self) -> dict[str, object]:
        """What the API shows: never the hidden tests or the reference fix."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "request": self.request,
            "expected_files": self.expected_files,
        }


def _rel_files(base: Path) -> list[str]:
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def load_cases(ids: list[str] | None = None, base: Path = CASES_DIR) -> list[Case]:
    cases = []
    for toml in sorted(base.glob("*/case.toml")):
        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        cases.append(
            Case(
                id=data["id"],
                title=data["title"],
                category=data["category"],
                request=data["request"],
                expected_files=list(data.get("expected_files", [])),
                root=toml.parent,
            )
        )
    if ids:
        known = {c.id for c in cases}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise KeyError(f"unknown case(s): {', '.join(unknown)}")
        cases = [c for c in cases if c.id in ids]
    return cases


def copy_tree(src: Path, dst: Path) -> None:
    for rel in _rel_files(src):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / rel, target)


def materialise(case: Case, dest: Path, *, with_fix: bool = False) -> Path:
    """Write the case's repository to `dest` as a git repo with one commit. Returns dest."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    copy_tree(case.repo_dir, dest)
    run_git(dest, ["init", "-q", "-b", "main"])
    run_git(dest, ["add", "-A"])
    run_git(
        dest, ["commit", "-q", "--no-verify", "-m", f"bench: {case.id}"], extra_config=_IDENTITY
    )
    if with_fix:
        copy_tree(case.fix_dir, dest)
    return dest


def reference_patch(case: Case, scratch: Path) -> str:
    """The reference fix as a unified diff against the buggy repo."""
    repo = materialise(case, scratch / f"ref-{case.id}", with_fix=True)
    run_git(repo, ["add", "-A"])
    return run_git(
        repo, ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD"]
    ).text
