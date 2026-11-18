"""What a training run is asked to do (ADR-0014 rules 1, 2, 5 and 8).

Torch-free, so the API can validate a request without PyTorch. The models
themselves are built by :func:`stratal.train.reference.build_model`, in the
images that have PyTorch.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReferenceModel = Literal["mlp", "small_cnn"]
"""Models defined in code until the model builder (M9) compiles its own specs."""

ALGORITHM_VERSION = 1
"""Bumped whenever the training loop changes what a run with the same config produces."""


class ModelSpec(BaseModel):
    """A model to train. In M8 it names a reference model; from M9 the builder's graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ReferenceModel = Field(
        description="'mlp': two hidden layers (256, 128) on the flattened image. "
        "'small_cnn': three blocks of two 3×3 convolutions with batch norm, then a "
        "global average pool and one linear layer."
    )


class TrainConfig(BaseModel):
    """Settings for the train stage. Every field is recorded with the run (NFR-4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: ModelSpec
    epochs: int = Field(default=10, ge=1, le=1000, description="Passes over the train split.")
    batch_size: int = Field(default=128, ge=1, le=4096, description="Images per step.")
    optimizer: Literal["adam", "sgd"] = Field(default="adam", description="Adam, or SGD.")
    learning_rate: float = Field(default=1e-3, gt=0, le=1, description="Initial learning rate.")
    momentum: float = Field(default=0.9, ge=0, lt=1, description="SGD only.")
    weight_decay: float = Field(default=0.0, ge=0, le=1, description="L2 penalty.")
    schedule: Literal["constant", "cosine"] = Field(
        default="cosine",
        description="'cosine' anneals the learning rate to zero over the run, step by step.",
    )
    seed: int = Field(
        default=0, ge=0, description="Initial weights, dropout and each epoch's data order."
    )
    deterministic: bool = Field(
        default=False,
        description="Use only deterministic GPU kernels: slower, but a resumed run then "
        "matches an uninterrupted one exactly (ADR-0014 rule 5).",
    )
    device: Literal["auto", "cpu", "gpu"] = Field(
        default="auto",
        description="'gpu' and 'cpu' are used as given. 'auto' uses the GPU, except that a "
        "model under 1 M parameters times 20 steps on each device and trains on the faster "
        "(RUN-1.9); the result is cached per model, input shape and batch size.",
    )
    loader_workers: int | None = Field(
        default=None,
        ge=0,
        le=8,
        description="Processes decoding images. Null: 4 on the GPU worker, 1 on the CPU "
        "worker (spike S-08).",
    )
    checkpoint_every_s: int = Field(
        default=300,
        ge=1,
        le=3600,
        description="A checkpoint is written after every epoch and whenever this many "
        "seconds have passed since the last one (ADR-0014 rule 5, OC-3).",
    )
    algorithm_version: int = Field(default=ALGORITHM_VERSION, json_schema_extra={"readOnly": True})


class TrainRequest(BaseModel):
    """``POST /jobs`` with ``kind: train`` (RUN-1.1, ADR-0014 rule 1)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["train"]
    dataset_version: str = Field(
        description="A split version of a labelled-images dataset, e.g. ds_f8e67211dd94."
    )
    config: TrainConfig
