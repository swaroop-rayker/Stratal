"""The inference service's HTTP side (REG-1.4, ADR-0016 rule 1).

Reachable only on the Compose network; clients go through the API's
``POST /api/v1/models/{version_id}/predict``, which forwards here. Errors are
the same RFC 9457 problem documents as the API's (ADR-0011), so the API can pass
them through unchanged.
"""

from typing import Any, Protocol

from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stratal.api.problem import install_error_handlers


class Predictor(Protocol):
    """What the app needs from :class:`stratal.inference.holder.ModelHolder`."""

    def predict(self, version_id: str, image: Image.Image) -> dict[str, Any]: ...
    def check_input(self, version_id: str, given: str) -> None: ...
    def predict_values(
        self, version_id: str, *, record: dict[str, Any] | None = None, text: str | None = None
    ) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...


ImageDecoder = Any
"""``(image_b64=, blob=) -> PIL image``; :func:`stratal.inference.holder.decode_image`."""


class PredictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(pattern=r"^mv_[0-9a-z]{12}$")
    image_b64: str | None = Field(default=None, description="An image file, base64-encoded.")
    blob: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="A file of the data folder's blob store, by its SHA-256.",
    )
    record: dict[str, Any] | None = Field(
        default=None,
        description="A table model's input: one value per feature column, by name; null to "
        "impute (ADR-0019 rule 21).",
    )
    text: str | None = Field(default=None, description="A text model's input.")

    @model_validator(mode="after")
    def _one_input(self) -> "PredictBody":
        given = [n for n in ("image", "record", "text") if self._given(n)]
        if len(given) != 1:
            raise ValueError("send exactly one input: image_b64 or blob, record, or text")
        return self

    def _given(self, name: str) -> bool:
        if name == "image":
            return self.image_b64 is not None or self.blob is not None
        return getattr(self, name) is not None


def create_inference_app(holder: Predictor, decode: ImageDecoder) -> FastAPI:
    app = FastAPI(title="Stratal inference", docs_url=None, redoc_url=None)
    install_error_handlers(app)

    @app.post("/predict")
    def predict(body: PredictBody) -> dict[str, Any]:
        if body.record is not None or body.text is not None:
            return holder.predict_values(body.version_id, record=body.record, text=body.text)
        holder.check_input(body.version_id, "image")  # before reading an image it would refuse
        image = decode(image_b64=body.image_b64, blob=body.blob)
        return holder.predict(body.version_id, image)

    @app.get("/status")
    def status() -> dict[str, Any]:
        return holder.status()

    return app
