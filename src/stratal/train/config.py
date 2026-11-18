"""What a training run is asked to train (ADR-0014 rules 1 and 2).

Torch-free, so the API can validate a request without PyTorch. The models
themselves are built by :func:`stratal.train.reference.build_model`, in the
images that have PyTorch.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReferenceModel = Literal["mlp", "small_cnn"]
"""Models defined in code until the model builder (M9) compiles its own specs."""


class ModelSpec(BaseModel):
    """A model to train. In M8 it names a reference model; from M9 the builder's graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ReferenceModel = Field(
        description="'mlp': two hidden layers (256, 128) on the flattened image. "
        "'small_cnn': three blocks of two 3×3 convolutions with batch norm, then a "
        "global average pool and one linear layer."
    )
