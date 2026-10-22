"""What is this repository? Languages, frameworks, test runner, entry points."""

import json
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field

from verifix.repos.files import list_files
from verifix.repos.paths import RepoJail

_LANG_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".sql": "SQL",
    ".sh": "Shell",
    ".html": "HTML",
    ".css": "CSS",
}

_PY_FRAMEWORKS = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic",
    "streamlit": "Streamlit",
    "langchain": "LangChain",
    "pytest": "pytest",
    "celery": "Celery",
}
_JS_FRAMEWORKS = {
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "express": "Express",
    "@nestjs/core": "NestJS",
    "vite": "Vite",
    "jest": "Jest",
    "vitest": "Vitest",
}
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


@dataclass
class Overview:
    languages: dict[str, int]
    frameworks: list[str]
    test_runner: str | None
    manifests: list[str]
    entry_points: list[str]
    has_dockerfile: bool
    file_count: int
    test_dirs: list[str] = field(default_factory=list)


def _python_deps(jail: RepoJail, paths: set[str]) -> set[str]:
    deps: set[str] = set()
    if "pyproject.toml" in paths:
        try:
            data = tomllib.loads((jail.root / "pyproject.toml").read_text(encoding="utf-8"))
            proj = data.get("project", {})
            items = list(proj.get("dependencies", []))
            for extra in proj.get("optional-dependencies", {}).values():
                items += list(extra)
            poetry = data.get("tool", {}).get("poetry", {})
            items += list(poetry.get("dependencies", {}).keys())
            for item in items:
                m = _REQ_NAME.match(str(item))
                if m:
                    deps.add(m.group(1).lower())
        except (OSError, tomllib.TOMLDecodeError):
            pass
    for req in [p for p in paths if re.fullmatch(r"(requirements[\w\-]*\.txt)", p)]:
        try:
            for line in (jail.root / req).read_text(encoding="utf-8").splitlines():
                m = _REQ_NAME.match(line)
                if m and not line.strip().startswith(("#", "-")):
                    deps.add(m.group(1).lower())
        except OSError:
            pass
    return deps


def _js_deps(jail: RepoJail, paths: set[str]) -> tuple[set[str], dict[str, str]]:
    if "package.json" not in paths:
        return set(), {}
    try:
        data = json.loads((jail.root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), {}
    deps = set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))
    scripts = data.get("scripts", {}) if isinstance(data.get("scripts"), dict) else {}
    return deps, scripts


def overview(jail: RepoJail) -> Overview:
    files = list_files(jail).files
    paths = {f.path for f in files}
    langs: Counter[str] = Counter()
    for f in files:
        for ext, lang in _LANG_BY_EXT.items():
            if f.path.lower().endswith(ext):
                langs[lang] += 1
                break

    py = _python_deps(jail, paths)
    js, scripts = _js_deps(jail, paths)
    frameworks = sorted(
        {name for key, name in _PY_FRAMEWORKS.items() if key in py}
        | {name for key, name in _JS_FRAMEWORKS.items() if key in js}
    )

    test_dirs = sorted(
        {
            f.path.split("/")[0]
            for f in files
            if f.path.split("/")[0] in ("tests", "test", "__tests__")
        }
    )
    has_py_tests = any(re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$", f.path) for f in files)
    if "pytest" in py or (has_py_tests and langs.get("Python")):
        runner: str | None = "pytest"
    elif "vitest" in js:
        runner = "vitest"
    elif "jest" in js:
        runner = "jest"
    else:
        runner = None

    manifests = sorted(
        p
        for p in paths
        if p
        in {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "package.json",
            "go.mod",
            "Cargo.toml",
            "pom.xml",
            "build.gradle",
        }
        or re.fullmatch(r"requirements[\w\-]*\.txt", p)
    )
    entry_candidates = ["main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "server.py"]
    entries = sorted(p for p in paths if p.split("/")[-1] in entry_candidates and p.count("/") <= 2)
    if "start" in scripts:
        entries.append(f"package.json: npm start → {scripts['start']}")

    return Overview(
        languages=dict(langs.most_common()),
        frameworks=frameworks,
        test_runner=runner,
        manifests=manifests,
        entry_points=entries,
        has_dockerfile=any(p.split("/")[-1].lower().startswith("dockerfile") for p in paths),
        file_count=len(files),
        test_dirs=test_dirs,
    )
