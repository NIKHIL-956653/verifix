# Evaluation

The differentiator of this project: the agent is measured, not demoed.

## Principles

1. **Validate the ruler before trusting the score.** Every metric gets a known-answer
   test: a case where the right score is known in advance. (Lesson from rag-curriculum,
   where a whitespace-sensitive substring check reported 38 of 41 "misses" that had in
   fact been retrieved.)
2. **Outcomes come from execution, not from the model.** Task success = hidden
   acceptance tests pass in the sandbox. The model's confidence is recorded but never scored.
3. **Same tasks, different strategies.** Prompting, retrieval and tool-selection variants
   run against the identical benchmark, so differences are attributable.
4. **Replayable.** Model responses are recorded (cassettes, as in the whatsappOS eval
   harness), so a run can be replayed without cost and a regression can be bisected.

## Metrics (M7)

| Metric | Definition |
|---|---|
| Task success | Fraction of tasks whose hidden acceptance tests pass after the agent's patch |
| Regression rate | Fraction of tasks where a previously passing test fails after the patch |
| Patch precision | Files and lines changed vs. the reference fix; unrelated changes counted |
| Evidence coverage | Fraction of report claims that cite an existing evidence id |
| Tool reliability | Tool errors per task; recovered vs. unrecovered |
| Recovery | Success rate on tasks with injected faults (failing test, invalid patch, API 529, timeout) |
| Cost and effort | Model calls, input/output/cached tokens, USD, tool calls, wall time per task |

## Benchmark (M7)

Small fixture repositories, each with one seeded bug, a hidden acceptance test, a
reference minimal fix, and a task statement written like a real bug report. Target: 20–30
tasks across bug classes (missing validation → 500, off-by-one, missing auth check,
wrong exception handling, SQL correctness, a concurrency bug). Later: an SWE-bench Lite
subset for an external comparison.

## Results

None yet. Results get recorded here as they are produced, with the commit hash, model,
date and cost of each run. Nothing will be reported here that was not produced by a run.

### M0 baseline facts (22 Oct 2026)

- Test suite: 49 tests, all passing, about 2 s. One test needs Postgres and skips cleanly without it.
- No test opens a non-loopback network connection. This is enforced by `tests/conftest.py`
  and proven by `test_network_guard_blocks_real_hosts`.
