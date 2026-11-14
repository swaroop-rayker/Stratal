"""Run one supervisor in its own process: ``python -m stratal.testing.run_supervisor JOB IDX``.

For tests that must freeze or kill a supervisor from outside. It prints the
outcome and never enqueues a following stage.
"""

import sys

from stratal.core.logging import configure_logging
from stratal.runtime.supervisor import run_stage


def main() -> int:
    configure_logging("WARNING", sys.stderr)
    print(run_stage(sys.argv[1], int(sys.argv[2]), enqueue=lambda _item: None), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
