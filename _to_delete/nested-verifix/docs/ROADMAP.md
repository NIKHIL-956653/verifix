# Roadmap

Each milestone has acceptance criteria that are checked by tests or by a recorded run.
"Done" means those checks pass. Writing the code is not enough.

## M0 Foundation — done (22 Oct 2026)

- [x] `pip install -e ".[dev]"`, `ruff check` clean, `pytest` green (49 tests)
- [x] No test can reach the network. Enforced by conftest and proven by a test
- [x] `docker compose up -d` → Postgres (dev + test DBs) on 127.0.0.1:5434
- [x] `alembic upgrade head` creates `repos`, `tasks`, `evidence`. A real-Postgres test
      round-trips rows and proves the DB rejects bad statuses, bad hashes and duplicate sequence numbers
- [x] `GET /health` reports db and key status, never the key (tested)
- [x] LLM boundary: protocol, Anthropic adapter (request shape, parsing, error mapping
      tested against a mock transport), FakeLLM, retry + circuit breaker (tested with a fake clock)
- [x] structlog with redaction. A test proves an API key never reaches a log line, including tracebacks
- [x] `web/`: React + TS + Vite shell showing `/health` through the dev proxy
- [x] docs: README, ARCHITECTURE, ROADMAP, SECURITY, EVALUATION, API, DECISIONS

## M1 Repository Explorer — next

- [ ] Register a local repo by path; non-git paths rejected; stored as a resolved absolute path
- [ ] File list = `git ls-files -co --exclude-standard` minus the secret deny-list
- [ ] `read_source_file(path, start, end)`: numbered lines; rejects `..`, absolute paths,
      symlink escapes, binaries and files over the size cap (one test each)
- [ ] `search(pattern, regex)`: `file:line: text`, capped, deny-list applied
- [ ] Read-only `git status` / `git diff`: working-tree hash identical before and after, on a dirty tree
- [ ] Language/framework detection (pyproject, requirements, package.json, Dockerfile)
- [ ] Python symbol index (classes and functions with line ranges)
- [ ] Measured against a real repo (eap): file count, index time, search latency → EVALUATION.md
- [ ] UI: repo picker, file tree, file viewer with line numbers, search

## Later

- **M2 Read-only AI engineer**: answers questions about a repo citing `file:line` evidence ids
- **M3 Planner and debugger**: structured plan, hypotheses, bounded tool loop, budgets, cancellation
- **M4 Patch and diff**: task worktrees, unified diffs, validation, approval, safe rollback
- **M5 Sandboxed testing**: Docker runner with no network and resource limits, real exit codes
- **M6 Code review**: findings with severity, location, evidence and fix; no speculative "vulnerabilities"
- **M7 Reliability benchmark**: 20–30 seeded-bug tasks, metrics in EVALUATION.md, dashboard
- **M8 GitHub integration**: authorised import, branches, CI status, approved PR creation
- **M9 Portfolio release**: docs, reproducible setup, benchmark results, demo scenarios
