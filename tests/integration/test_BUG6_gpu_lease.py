"""BUG6 and ADR-0006 rule 2: one GPU holder at a time, across processes."""

import os
import signal
import subprocess
import sys

import pytest

from stratal.core.errors import GpuBusy
from stratal.db.engine import get_engine
from stratal.runtime.gpu_lease import current_holder, gpu_lease
from stratal.testing.harness import wait_until

pytestmark = pytest.mark.integration

_TRY = """
import sys
from stratal.core.errors import GpuBusy
from stratal.db.engine import get_engine
from stratal.runtime.gpu_lease import gpu_lease
try:
    with gpu_lease(get_engine(), holder="second"):
        print("taken")
except GpuBusy as exc:
    print(exc.code, exc.detail)
    sys.exit(3)
"""

_HOLD = """
import time
from stratal.db.engine import get_engine
from stratal.runtime.gpu_lease import gpu_lease
with gpu_lease(get_engine(), holder="doomed"):
    print("holding", flush=True)
    time.sleep(120)
"""


def _python(code: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ.copy(),
    )


def test_BUG6_a_second_process_cannot_take_the_gpu_lease(migrated: None) -> None:
    with gpu_lease(get_engine(), holder="job_0042/label"):
        assert current_holder(get_engine()) == "job_0042/label"
        second = _python(_TRY)
        output, _ = second.communicate(timeout=60)

    assert second.returncode == 3, output
    assert "gpu_busy" in output
    assert "job_0042/label" in output, "the refusal names who holds the GPU"
    assert current_holder(get_engine()) is None


def test_BUG6_the_lease_is_free_again_after_the_block(migrated: None) -> None:
    with gpu_lease(get_engine(), holder="first"):
        pass

    with gpu_lease(get_engine(), holder="again"):
        assert current_holder(get_engine()) == "again"


def test_BUG6_a_holder_that_dies_releases_the_lease(migrated: None) -> None:
    holder = _python(_HOLD)
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "holding"
    with pytest.raises(GpuBusy), gpu_lease(get_engine(), holder="impatient"):
        pass

    os.kill(holder.pid, signal.SIGKILL)
    holder.wait(10)
    wait_until(lambda: current_holder(get_engine()) is None, what="PostgreSQL to end the session")

    with gpu_lease(get_engine(), holder="next", wait_s=5):
        assert current_holder(get_engine()) == "next"
