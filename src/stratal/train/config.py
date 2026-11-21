"""What a training run is asked to do (ADR-0014 rules 1, 2, 5 and 8).

Torch-free, so the API can validate a request without PyTorch. The models
themselves are built by :func:`stratal.train.reference.build_model`, in the
images that have PyTorch.
"""

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from stratal.build.spec import GraphSpec, identity
from stratal.ml.params import ClassicalConfig

ReferenceModel = Literal["mlp", "small_cnn"]
"""Models defined in code (M8). Since M9 the builder's graphs train beside them."""

ALGORITHM_VERSION = 1
"""Bumped whenever the training loop changes what a run with the same config produces."""


class ModelSpec(BaseModel):
    """A model to train: a reference model by name, or the builder's graph (ADR-0017 rule 2).

    A reference spec serializes exactly as in M8 (``{"name": "small_cnn"}``), so the
    config hashes and lineage keys of runs before M9 do not change.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["mlp", "small_cnn", "graph"] = Field(
        description="'mlp': two hidden layers (256, 128) on the flattened image. "
        "'small_cnn': three blocks of two 3×3 convolutions with batch norm, then a "
        "global average pool and one linear layer. 'graph': the model in `graph`, "
        "built on SCR-08."
    )
    graph: GraphSpec | None = Field(default=None, description="The layers and edges, for 'graph'.")

    @model_validator(mode="after")
    def _graph_iff_named(self) -> "ModelSpec":
        if (self.name == "graph") != (self.graph is not None):
            raise ValueError("'graph' is given with name 'graph', and only then")
        return self

    @model_serializer(mode="wrap")
    def _without_empty_graph(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("graph") is None:
            data.pop("graph", None)
        return data

    @property
    def display(self) -> str:
        """What messages and lineage names call it: the graph's label, or the name."""
        return self.graph.label if self.graph is not None else self.name

    def identity(self) -> dict[str, Any]:
        """The spec as it enters a lineage key: a graph without its label (ADR-0017 rule 1)."""
        if self.graph is None:
            return self.model_dump(mode="json")
        return {"name": "graph", "graph": identity(self.graph)}


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
    init_from: str | None = Field(
        default=None,
        pattern=r"^mv_[0-9a-f]{12}$",
        description="Start from this trained version's weights instead of the lineage's "
        "base (RUN-1.10, ADR-0014 Addendum A). The model must be that version's.",
    )
    reset_head: bool = Field(
        default=False,
        description="With init_from: re-initialise only the locked output layer, so a split "
        "with other classes can be fine-tuned (RUN-1.10.AC3).",
    )
    freeze: list[str] = Field(
        default_factory=lambda: list[str](),
        max_length=500,
        description="With init_from: graph node IDs (or 'head') whose weights stay as the "
        "parent's; they are held in eval mode, so BatchNorm statistics stay too (RUN-1.10.AC2).",
    )

    @field_validator("freeze")
    @classmethod
    def _sorted_once(cls, value: list[str]) -> list[str]:
        return sorted(set(value))

    @model_validator(mode="after")
    def _needs_a_parent(self) -> "TrainConfig":
        if self.init_from is None and (self.reset_head or self.freeze):
            raise ValueError("reset_head and freeze need init_from (RUN-1.10)")
        return self

    @model_serializer(mode="wrap")
    def _without_fresh_start(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """A run that starts fresh serializes exactly as before M11, so the config hashes
        of earlier runs, and of every run without ``init_from``, do not change."""
        data: dict[str, Any] = handler(self)
        if data.get("init_from") is None:
            for name in ("init_from", "reset_head", "freeze"):
                data.pop(name, None)
        return data


class TrainRequest(BaseModel):
    """``POST /jobs`` with ``kind: train`` (RUN-1.1, ADR-0014 rule 1)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["train"]
    dataset_version: str = Field(
        description="A split version: of a labelled-images dataset for a network, of a "
        "tabular dataset for a classical model, e.g. ds_f8e67211dd94."
    )
    config: TrainConfig | None = Field(
        default=None, description="A network's run (RUN-1.1). Give this or 'classical'."
    )
    classical: ClassicalConfig | None = Field(
        default=None,
        description="A classical model's run on a tabular split (ML-1.1, ADR-0019 rule 15); "
        "it runs on the CPU queue. Give this or 'config'.",
    )

    @model_validator(mode="after")
    def _one_kind(self) -> "TrainRequest":
        if (self.config is None) == (self.classical is None):
            raise ValueError("give exactly one of 'config' (a network) and 'classical'")
        return self
