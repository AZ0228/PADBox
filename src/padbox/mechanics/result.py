"""Three-valued results for requirements that can be unresolved."""

from collections.abc import Iterable
from enum import StrEnum


class CheckResult(StrEnum):
    """Pass, fail, or unknown. Unknown is never treated as a pass."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


def combine_checks(results: Iterable[CheckResult]) -> CheckResult:
    """Combine requirement results. A known failure stays a failure."""
    found_unknown = False
    for result in results:
        if result is CheckResult.FAIL:
            return CheckResult.FAIL
        if result is CheckResult.UNKNOWN:
            found_unknown = True
    if found_unknown:
        return CheckResult.UNKNOWN
    return CheckResult.PASS
