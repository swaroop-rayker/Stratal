"""The inference service's HTTP side (REG-1.4, ADR-0016 rule 1).

Reachable only on the Compose network; clients go through the API's
``POST /api/v1/models/{version_id}/predict``, which forwards here. Errors are
the same RFC 9457 problem documents as the API's (ADR-0011), so the API can pass
them through unchanged.
"""

from typing import Any, Protocol

from fastapi import FastAPI
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from stratal.api.problem import install_error_handlers


class Predictor(Protocol):
    """What the app needs from :class:`stratal.inference.holder.ModelHolder`."""

    def predict(self, version_id: str, image: Image.Image) -> dict[str, Any]: ...
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


def create_inference_app(holder: Predictor, decode: ImageDecoder) -> FastAPI:
    app = FastAPI(title="Stratal inference", docs_url=None, redoc_url=None)
    install_error_handlers(app)

    @app.post("/predict")
    def predict(body: PredictBody) -> dict[str, Any]:
        image = decode(image_b64=body.image_b64, blob=body.blob)
        return holder.predict(body.version_id, image)

    @app.get("/status")
    def status() -> dict[str, Any]:
        return holder.status()

    return app
