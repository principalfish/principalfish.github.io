"""Failure reporting for independently committed historical model dates."""

from __future__ import annotations


class HistoryRecomputationError(RuntimeError):
    """A historical batch completed with one or more failed model dates."""

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        self.failures = tuple(failures)
        details = "; ".join(f"{day}: {message}" for day, message in failures)
        super().__init__(
            f"History recomputation failed for {details}. "
            "Successful replacements and previous failed-date results are retained."
        )
