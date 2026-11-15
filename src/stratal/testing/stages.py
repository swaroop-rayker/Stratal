"""A synthetic stage for tests of the job runtime.

It does no real work: it counts to ``items`` at a steady pace and can be told
to warn, raise, kill itself or ignore a cancel request. Registered only when
this module is named in ``STRATAL_EXTRA_STAGES``.
"""

import os
import time

from pydantic import BaseModel, ConfigDict

from stratal.core.errors import StratalError
from stratal.runtime.stages import StageDef, StageResult, StageRun, register

NAME = "synthetic"


class SyntheticConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: int = 20
    seconds_per_item: float = 0.01
    warn_at: int | None = None
    fail_at: int | None = None
    typed_failure: bool = False
    kill_signal_at: int | None = None
    kill_signal: int = 9
    ignore_cancel: bool = False
    label: str = "synthetic-base"


def _run(run: StageRun) -> StageResult:
    config = SyntheticConfig.model_validate(run.config)
    ctx = run.ctx
    ctx.emit(stage=ctx.stage, message=f"Preparing {config.items} items · {config.label}")
    for step in range(1, config.items + 1):
        if not config.ignore_cancel:
            ctx.raise_if_cancelled()
        time.sleep(config.seconds_per_item)
        if step == config.kill_signal_at:
            os.kill(os.getpid(), config.kill_signal)
        if step == config.fail_at:
            if config.typed_failure:
                raise StratalError("planted typed failure", suggested_action="remove fail_at")
            raise RuntimeError("planted failure")
        if step == config.warn_at:
            ctx.emit(
                stage=ctx.stage,
                level="WARN",
                message=f"Item {step} was slow",
                reason="planted warning",
                suggested_action="nothing; this is a test",
            )
        ctx.emit(
            stage=ctx.stage,
            substage="work",
            message=f"Processing item {step}/{config.items} · {config.label} · "
            f"delay={config.seconds_per_item}s",
            step=step,
            total=config.items,
            metrics={"items_done": step},
        )
    return StageResult(metrics={"items": config.items})


register(StageDef(NAME, "cpu", SyntheticConfig, _run))
register(StageDef("synthetic2", "cpu", SyntheticConfig, _run))
