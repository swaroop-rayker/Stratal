"""RUN-1.4 v2: closing a training state frees gradients, optimizer state and weights.

Plain fakes stand in for PyTorch, which the dev image does not have; the real
card is checked in ``tests/gpu/test_RUN_1_4_training_unload.py``. Strict expected
failures until the owner writes ``TrainingState.close`` (ADR-0012).
"""

from typing import Any

import pytest

from stratal.label.models import ModelInfo
from stratal.train.memory import TrainingState

owner_unit = pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="owner unit")

INFO = ModelInfo(name="small_cnn", revision="spec-1", display="small CNN", dtype="fp32")


class FakeParameter:
    def __init__(self) -> None:
        self.grad: object | None = object()


class FakeModel:
    def __init__(self, count: int = 3) -> None:
        self._parameters = [FakeParameter() for _ in range(count)]

    def parameters(self) -> list[FakeParameter]:
        return self._parameters


class FakeAdam:
    """Keeps per-parameter state as torch.optim.Optimizer does: ``state`` and ``param_groups``."""

    def __init__(self, model: FakeModel) -> None:
        self.param_groups: list[dict[str, Any]] = [{"params": model.parameters(), "lr": 1e-3}]
        self.state: dict[Any, dict[str, object]] = {
            p: {"exp_avg": object(), "exp_avg_sq": object(), "step": 3} for p in model.parameters()
        }

    def zero_grad(self, set_to_none: bool = True) -> None:
        for group in self.param_groups:
            for p in group["params"]:
                p.grad = None if set_to_none else p.grad


@owner_unit
def test_RUN_1_4_close_frees_gradients_and_optimizer_state() -> None:
    model = FakeModel()
    optimizer = FakeAdam(model)
    parameters = model.parameters()
    state = TrainingState(model, optimizer, info=INFO)

    state.close()

    assert all(p.grad is None for p in parameters), "gradients set to None, not zeroed"
    assert not optimizer.state, "the optimizer's per-parameter state is emptied"
    assert state.model is None and state.optimizer is None


@owner_unit
def test_RUN_1_4_closing_twice_is_harmless() -> None:
    state = TrainingState(FakeModel(), FakeAdam(FakeModel()), info=INFO)
    state.close()
    state.close()
    assert state.model is None


def test_RUN_1_4_a_training_state_is_what_the_vram_manager_loads() -> None:
    """Plumbing, not the owner unit: the state has the manager's ``info`` and ``close``."""
    state = TrainingState(FakeModel(), FakeAdam(FakeModel()), info=INFO)
    assert state.info.name == "small_cnn"
    assert callable(state.close)


@owner_unit
def test_RUN_1_4_close_accepts_a_model_without_an_optimizer() -> None:
    """Contract rule 6 (added 2026-11-18 for the inference service, ADR-0016): a model
    held for inference has no optimizer."""
    model = FakeModel()
    state = TrainingState(model, None, info=INFO)

    state.close()
    state.close()

    assert state.model is None and state.optimizer is None
