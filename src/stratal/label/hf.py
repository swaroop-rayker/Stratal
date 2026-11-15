# pyright: basic, reportMissingImports=false
# PyTorch and transformers exist only in the GPU image, so this one module is
# checked in basic mode (DECISIONS.md, "untyped libraries behind one module each").
"""PyTorch and transformers behind the protocols of :mod:`stratal.label.models`.

Everything loads from a pulled, verified folder (:func:`local_path`) with the hub
off (NFR-7) and the native transformers classes, never ``trust_remote_code``
(NFR-8, ADR-0009). Each adapter turns PyTorch's out-of-memory error into
:class:`CudaOutOfMemory` after freeing what the failed call allocated.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

import torch
from PIL import Image
from transformers import (
    AutoProcessor,
    BlipForConditionalGeneration,
    BlipProcessor,
    CLIPModel,
    CLIPProcessor,
    Florence2ForConditionalGeneration,
)

from stratal.core.errors import CudaUnavailable, ValidationFailed
from stratal.label.models import CudaOutOfMemory, ModelInfo, Vector
from stratal.pretrained.catalog import get_model, local_path

DType = Literal["fp16", "fp32"]
_TORCH_DTYPES = {"fp16": torch.float16, "fp32": torch.float32}
_DISPLAY = {
    "florence-2-base": "Florence-2-base",
    "blip-large": "BLIP-large",
    "clip-vit-b-32-laion2b": "CLIP ViT-B/32 (LAION-2B)",
}


def require_cuda() -> None:
    if not torch.cuda.is_available():
        raise CudaUnavailable(
            "PyTorch cannot reach the GPU.",
            suggested_action="If the laptop slept, restart WSL (wsl --shutdown), then retry.",
        )


def set_vram_cap(fraction: float) -> None:
    """ADR-0006 rule 8: cap PyTorch's allocator before the first allocation."""
    require_cuda()
    torch.cuda.set_per_process_memory_fraction(fraction)


class TorchGpu:
    """The real :class:`~stratal.label.models.GpuBackend`."""

    def allocated_mb(self) -> float:
        return torch.cuda.memory_allocated() / 2**20

    def reserved_mb(self) -> float:
        return torch.cuda.memory_reserved() / 2**20

    def free_mb(self) -> float:
        free, _total = torch.cuda.mem_get_info()
        return free / 2**20

    def total_mb(self) -> float:
        _free, total = torch.cuda.mem_get_info()
        return total / 2**20

    def synchronize(self) -> None:
        torch.cuda.synchronize()

    def empty_cache(self) -> None:
        torch.cuda.empty_cache()


def _guarded[T](batch_size: int, call: Callable[[], T]) -> T:
    """Run ``call``; on PyTorch OOM free the cache and raise :class:`CudaOutOfMemory`.

    The error is raised outside the ``except`` block so its traceback does not
    keep the failed call's tensors alive.
    """
    detail: str | None = None
    try:
        with torch.inference_mode():
            return call()
    except torch.cuda.OutOfMemoryError as exc:
        detail = str(exc).splitlines()[0][:300]
    torch.cuda.empty_cache()
    raise CudaOutOfMemory(batch_size, detail)


def _info(name: str, dtype: DType) -> ModelInfo:
    model = get_model(name)
    return ModelInfo(name, model.revision, _DISPLAY.get(name, name), dtype)


def _rgb(images: Sequence[Image.Image]) -> list[Image.Image]:
    return [image if image.mode == "RGB" else image.convert("RGB") for image in images]


class FlorenceCaptioner:
    def __init__(
        self, folder: Path, info: ModelInfo, *, task: str, max_new_tokens: int, num_beams: int
    ) -> None:
        self._info = info
        self._dtype = _TORCH_DTYPES[info.dtype]
        self._task = task
        self._generate: dict[str, Any] = {"max_new_tokens": max_new_tokens, "num_beams": num_beams}
        self._processor: Any = AutoProcessor.from_pretrained(folder)
        self._model: Any = Florence2ForConditionalGeneration.from_pretrained(
            folder, dtype=self._dtype
        ).to("cuda")
        self._model.eval()

    @property
    def info(self) -> ModelInfo:
        return self._info

    def caption(self, images: Sequence[Image.Image]) -> list[str]:
        def run() -> list[str]:
            inputs = self._processor(
                text=[self._task] * len(images), images=_rgb(images), return_tensors="pt"
            ).to("cuda", self._dtype)
            output = self._model.generate(**inputs, **self._generate)
            texts = self._processor.batch_decode(output, skip_special_tokens=True)
            return [str(text).strip() for text in texts]

        return _guarded(len(images), run)

    def close(self) -> None:
        self._model = None
        self._processor = None


class BlipCaptioner:
    def __init__(
        self, folder: Path, info: ModelInfo, *, max_new_tokens: int, num_beams: int
    ) -> None:
        self._info = info
        self._dtype = _TORCH_DTYPES[info.dtype]
        self._generate: dict[str, Any] = {"max_new_tokens": max_new_tokens, "num_beams": num_beams}
        self._processor: Any = BlipProcessor.from_pretrained(folder)
        self._model: Any = BlipForConditionalGeneration.from_pretrained(
            folder, dtype=self._dtype
        ).to("cuda")
        self._model.eval()

    @property
    def info(self) -> ModelInfo:
        return self._info

    def caption(self, images: Sequence[Image.Image]) -> list[str]:
        def run() -> list[str]:
            inputs = self._processor(images=_rgb(images), return_tensors="pt").to(
                "cuda", self._dtype
            )
            output = self._model.generate(**inputs, **self._generate)
            texts = self._processor.batch_decode(output, skip_special_tokens=True)
            return [str(text).strip() for text in texts]

        return _guarded(len(images), run)

    def close(self) -> None:
        self._model = None
        self._processor = None


class HfClipEncoder:
    def __init__(self, folder: Path, info: ModelInfo) -> None:
        self._info = info
        self._dtype = _TORCH_DTYPES[info.dtype]
        self._processor: Any = CLIPProcessor.from_pretrained(folder)
        self._model: Any = CLIPModel.from_pretrained(folder, dtype=self._dtype).to("cuda")
        self._model.eval()

    @property
    def info(self) -> ModelInfo:
        return self._info

    def encode_images(self, images: Sequence[Image.Image]) -> list[Vector]:
        def run() -> list[Vector]:
            inputs = self._processor(images=_rgb(images), return_tensors="pt")
            pixels = inputs["pixel_values"].to("cuda", self._dtype)
            vision = self._model.vision_model(pixel_values=pixels)
            embeds = self._model.visual_projection(vision.pooler_output)
            return embeds.float().cpu().tolist()

        return _guarded(len(images), run)

    def encode_texts(self, texts: Sequence[str]) -> list[Vector]:
        def run() -> list[Vector]:
            inputs = self._processor(
                text=list(texts), return_tensors="pt", padding=True, truncation=True
            ).to("cuda")
            output = self._model.text_model(
                input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"]
            )
            embeds = self._model.text_projection(output.pooler_output)
            return embeds.float().cpu().tolist()

        return _guarded(len(texts), run)

    def close(self) -> None:
        self._model = None
        self._processor = None


def load_captioner(
    name: str,
    models_dir: Path,
    *,
    dtype: DType,
    task: str = "<CAPTION>",
    max_new_tokens: int = 40,
    num_beams: int = 3,
) -> FlorenceCaptioner | BlipCaptioner:
    require_cuda()
    folder = local_path(models_dir, name)
    info = _info(name, dtype)
    if name == "florence-2-base":
        return FlorenceCaptioner(
            folder, info, task=task, max_new_tokens=max_new_tokens, num_beams=num_beams
        )
    if name == "blip-large":
        return BlipCaptioner(folder, info, max_new_tokens=max_new_tokens, num_beams=num_beams)
    raise ValidationFailed(f"{name!r} is not a captioner.")


def load_clip(name: str, models_dir: Path, *, dtype: DType) -> HfClipEncoder:
    require_cuda()
    if get_model(name).role != "clip":
        raise ValidationFailed(f"{name!r} is not a CLIP model.")
    return HfClipEncoder(local_path(models_dir, name), _info(name, dtype))
