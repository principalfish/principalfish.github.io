"""Helpers for tests that need a background job to be running."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from console.shared import jobs
from console.shared.jobs import Job, JobOutcome

WAIT_SECONDS = 20


@contextmanager
def job_holding_the_slot() -> Iterator[Job]:
    """A real background job that holds the run slot until the block ends.

    Jobs run in a thread inside the block, whatever executor the test has, and
    the previous executor is back afterwards. The job is finished, and the slot
    free, once the block exits.
    """
    previous = jobs.set_executor(jobs.ThreadExecutor())
    started = threading.Event()
    release = threading.Event()

    def work() -> JobOutcome:
        started.set()
        assert release.wait(WAIT_SECONDS)
        return JobOutcome(
            {
                "title": "Held",
                "command": "held.py",
                "stdout": "",
                "stderr": "",
                "return_code": 0,
            },
            ok=True,
        )

    try:
        job = jobs.start_job(
            label="Held", section=None, dry_run=True, work=work, result_kind="command"
        )
        try:
            assert started.wait(WAIT_SECONDS)
            yield job
        finally:
            release.set()
            assert job.wait(WAIT_SECONDS)
    finally:
        jobs.set_executor(previous)
