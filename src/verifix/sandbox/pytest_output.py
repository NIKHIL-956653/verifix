"""Parse pytest terminal output into counts.

Counts are informational only; a run's status always comes from the exit code.
"""

import re

_SUMMARY = re.compile(r"^=+ (.+?) in [\d.]+s(?: \([^)]*\))? =+\s*$", re.M)
_QUIET_SUMMARY = re.compile(r"^(?:\d+ \w+(?:, )?)+ in [\d.]+s\s*$", re.M)
_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)")
_SHORT = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (\S+)", re.M)


def parse(output: str) -> dict[str, object]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    full = list(_SUMMARY.finditer(output))
    quiet = list(_QUIET_SUMMARY.finditer(output))
    line = full[-1].group(1) if full else (quiet[-1].group(0) if quiet else "")
    for n, word in _COUNT.findall(line):
        key = {"error": "errors", "errors": "errors"}.get(word, word)
        if key in counts:
            counts[key] += int(n)
    failing: list[str] = []
    passing: list[str] = []
    for status, node in _SHORT.findall(output):
        if status in ("FAILED", "ERROR"):
            failing.append(node)
        elif status == "PASSED":
            passing.append(node)
    return {**counts, "failing_tests": failing[:100], "passing_tests": passing[:300]}
