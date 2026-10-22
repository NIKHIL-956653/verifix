# Roadmap

"Done" means the acceptance checks pass in the test suite or a recorded run.

## Done (22 Oct 2026)

- **M0 Foundation**: settings with SecretStr, redaction, logging, async DB + migrations,
  LLM boundary with retry/circuit breaker/pricing, FakeLLM, network-blocked tests.
- **M1 Repository explorer**: register, path jail + secret deny-list, safe git, file
  list/read, search, Python symbols, overview; API + UI explorer.
- **M2 Read-only engineer**: `ask` / `investigate` tasks citing evidence ids.
- **M3 Planner / debugger**: plan with hypotheses and acceptance criteria, optional plan
  approval, bounded loop with budgets, cancel, retry, recovery after restart.
- **M4 Patch and diff**: per-task worktrees, exact-replace edits with diff previews,
  revert, approvals, conflict-checked apply, patch download.
- **M5 Sandbox**: Docker runner (no network, read-only, non-root, limits, timeout),
  runtime and dependency images, exit-code status.
- **M6 Review**: findings with severity, location, recommendation; "confirmed" requires a
  failing test.
- **M7 Benchmark**: 12 cases, grader, metrics, record/replay, CLI, API, dashboard; ruler validated.
- **M8 GitHub**: import by URL, draft PR from source_head + patch, CI checks.
- **M9**: React UI (tasks, approvals, timeline, evidence sheet, diff, report, repos,
  benchmark) and these docs.

## Next

- Grow the benchmark to 20–30 cases; add fault injection.
- JavaScript/TypeScript test runner in the sandbox.
- Streamed model output in the timeline.
