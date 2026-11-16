"""Stage 2: write a normalised, size-capped copy of every accepted image (DATA-6.1).

For each accepted row: decode the original, apply its EXIF orientation, convert
it to RGB, shrink it so the longer side is at most ``max_side`` (never
enlarge), and store the result as a JPEG in the blob store. The original stays
where it is; the row gains ``derived_sha256``, ``derived_width`` and
``derived_height``.

Every setting that shapes the output is part of the stage config, so it is
recorded in the version's config (DATA-6.1) and a different value gives a
different version ID. The same input and config produce the same bytes, which
the content-addressed blob store stores once, so a rerun adds no files.

Two passes, so the progress messages come before the commit's: pass 1 does the
work and records each result in the derivation cache, one batch at a time;
pass 2 reads the results back from the cache while the manifest is written.
"""

import io
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import PIL
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.versioning import VersionDeriver
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.storage.blobs import BlobStore
from stratal.storage.derivations import KIND_PREPROCESS, DerivationCache, params_hash
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_REJECTED, Row, iter_rows

STAGE = "preprocess"
_BATCH = 64
_ROW_BATCH = 2000


class PreprocessConfig(BaseModel):
    """Settings for the preprocess stage. Every field is recorded in the version (DATA-6.1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_side: int = Field(
        default=768,
        ge=32,
        le=8192,
        description="Longer side of the copy, in pixels, at most (DATA-6.1; 768 by OC-3). "
        "Smaller images are not enlarged.",
    )
    jpeg_quality: int = Field(default=95, ge=50, le=100, description="JPEG quality of the copy.")
    resample: Literal["lanczos"] = Field(default="lanczos", description="Resampling filter.")
    exif_transpose: Literal[True] = Field(
        default=True, description="The EXIF orientation is applied, so the copy is upright."
    )
    mode: Literal["RGB"] = Field(
        default="RGB",
        description="Colour mode of the copy. Transparency is flattened onto white.",
    )
    output_format: Literal["JPEG"] = Field(default="JPEG", description="File format of the copy.")


@dataclass(frozen=True)
class PreprocessResult:
    record: VersionRecord
    new_blob_bytes: int
    metrics: dict[str, MetricValue]
    seconds: float


@dataclass(frozen=True)
class Derived:
    sha256: str
    width: int
    height: int


def _flatten(image: Image.Image) -> Image.Image:
    """The image in RGB, with any transparency composited onto white."""
    if image.mode == "RGB":
        return image
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def target_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """The copy's size: scaled down to fit ``max_side``, aspect kept, never enlarged."""
    longer = max(width, height)
    if longer <= max_side:
        return width, height
    scale = max_side / longer
    return max(1, round(width * scale)), max(1, round(height * scale))


def transform(path: Path, config: PreprocessConfig) -> tuple[bytes, int, int]:
    """Decode, orient, convert and shrink one image; return the JPEG bytes and the size."""
    with Image.open(path) as opened:
        # For JPEG, decode at the smallest power-of-two reduction that is still at least
        # max_side on each side: far less work, and the resize below finishes the job.
        opened.draft("RGB", (config.max_side, config.max_side))
        oriented = ImageOps.exif_transpose(opened)
        rgb = _flatten(oriented)
        size = target_size(rgb.width, rgb.height, config.max_side)
        if size != rgb.size:
            rgb = rgb.resize(size, Image.Resampling.LANCZOS)  # pyright: ignore[reportUnknownMemberType]
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=config.jpeg_quality)
        return buffer.getvalue(), rgb.width, rgb.height


def cache_params(config: PreprocessConfig) -> str:
    """Everything that changes the output bytes, including the Pillow version that encodes them."""
    return params_hash(
        {
            "kind": KIND_PREPROCESS,
            "config": config.model_dump(mode="json"),
            "pillow": PIL.__version__,
        }
    )


@dataclass
class _Stats:
    done: int = 0
    computed: int = 0
    cache_hits: int = 0
    resized: int = 0
    failed: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """Original sha256 → reason, for images that could not be decoded here."""
    new_blob_bytes: int = 0
    max_side_seen: int = 0
    width_sum: int = 0
    height_sum: int = 0


class _Preprocessor:
    def __init__(
        self,
        *,
        data_dir: Path,
        config: PreprocessConfig,
        sink: EventSink,
        cache: DerivationCache,
        checkpoint: Callable[[], None],
    ) -> None:
        self.blobs = BlobStore(data_dir)
        self.config = config
        self.sink = sink
        self.cache = cache
        self.checkpoint = checkpoint
        self.params = cache_params(config)
        self.stats = _Stats()

    def _usable(self, value: Any) -> Derived | None:
        """A cached result, if its copy is still in the blob store (INV-04)."""
        try:
            derived = Derived(str(value["sha256"]), int(value["width"]), int(value["height"]))
        except (KeyError, TypeError, ValueError):
            return None
        return derived if self.blobs.exists(derived.sha256) else None

    def work(self, shas: list[str]) -> None:
        """Pass 1: make every missing copy, committing results to the cache per batch."""
        total = len(shas)
        label = f"Resizing to {self.config.max_side}px · normalizing"
        self.sink.emit(
            stage=STAGE,
            substage="resize",
            message=f"{label} · {self.config.mode} · {self.config.output_format} "
            f"q{self.config.jpeg_quality}",
            metrics={"images": total, "max_side": self.config.max_side},
        )
        stats = self.stats
        for start in range(0, total, _BATCH):
            self.checkpoint()
            batch = shas[start : start + _BATCH]
            found = self.cache.get_many(KIND_PREPROCESS, self.params, batch)
            fresh: dict[str, dict[str, Any]] = {}
            for sha in batch:
                cached = self._usable(found[sha]) if sha in found else None
                if cached is not None:
                    stats.cache_hits += 1
                    derived = cached
                else:
                    path = self.blobs.path_for(sha)
                    if not path.is_file():
                        raise StratalError(
                            f"The stored file {sha[:12]}… is missing from the data volume.",
                            suggested_action="Check that the stratal-data volume is the one "
                            "these versions were made on; re-ingest the source if it was lost.",
                        )
                    try:
                        data, width, height = transform(path, self.config)
                    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
                        detail = f"{type(exc).__name__}: {exc}".replace(
                            str(path), f"blob {sha[:12]}"
                        )
                        stats.failed[sha] = detail[:500]  # no local path (E-010)
                        continue
                    stored = self.blobs.put_bytes(data)
                    if stored.created:
                        stats.new_blob_bytes += stored.size_bytes
                    derived = Derived(stored.sha256, width, height)
                    fresh[sha] = {"sha256": derived.sha256, "width": width, "height": height}
                    stats.computed += 1
                stats.max_side_seen = max(stats.max_side_seen, derived.width, derived.height)
                stats.width_sum += derived.width
                stats.height_sum += derived.height
            self.cache.put_many(KIND_PREPROCESS, self.params, fresh)
            stats.done = min(start + _BATCH, total)
            self.sink.emit(
                stage=STAGE,
                substage="resize",
                message=f"{label} {stats.done:,}/{total:,}",
                step=stats.done,
                total=total,
                metrics={"computed": stats.computed, "cache_hits": stats.cache_hits},
            )

    def rows(self, parent_rows: Iterator[Row]) -> Iterator[Row]:
        """Pass 2: the parent's rows with the copies filled in from the cache."""
        batch: list[Row] = []
        for row in parent_rows:
            batch.append(row)
            if len(batch) >= _ROW_BATCH:
                yield from self._fill(batch)
                batch = []
        yield from self._fill(batch)

    def _fill(self, batch: list[Row]) -> Iterator[Row]:
        wanted = [r["sha256"] for r in batch if r["status"] == STATUS_ACCEPTED]
        found = self.cache.get_many(KIND_PREPROCESS, self.params, wanted)
        for row in batch:
            if row["status"] != STATUS_ACCEPTED:
                yield row
                continue
            sha = row["sha256"]
            if sha in self.stats.failed:
                row.update(
                    status=STATUS_REJECTED,
                    decided_at_stage=STAGE,
                    reason_code="corrupt_image",
                    reason_detail=self.stats.failed[sha],
                )
                yield row
                continue
            derived = self._usable(found.get(sha))
            if derived is None:
                raise StratalError(
                    f"The preprocessed copy of {row['row_key']!r} is missing after it was made.",
                    suggested_action="Retry the job; if it happens again, check the data volume.",
                )
            if max(derived.width, derived.height) < max(row["width"], row["height"]):
                self.stats.resized += 1
            row.update(
                derived_sha256=derived.sha256,
                derived_width=derived.width,
                derived_height=derived.height,
            )
            yield row


def run_preprocess(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: PreprocessConfig,
    derive: VersionDeriver,
    sink: EventSink,
    cache: DerivationCache,
    checkpoint: Callable[[], None] = lambda: None,
) -> PreprocessResult:
    """Write the copies for the parent version and commit the result as a ``preprocess`` version."""
    started = time.monotonic()
    worker = _Preprocessor(
        data_dir=data_dir, config=config, sink=sink, cache=cache, checkpoint=checkpoint
    )
    shas = sorted(
        {
            row["sha256"]
            for row in iter_rows(parent_manifest, columns=["sha256", "status"])
            if row["status"] == STATUS_ACCEPTED
        }
    )
    worker.work(shas)
    checkpoint()
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=worker.rows(iter_rows(parent_manifest)),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
    )
    stats = worker.stats
    made = max(len(shas) - len(stats.failed), 0)
    seconds = round(time.monotonic() - started, 3)
    metrics: dict[str, MetricValue] = {
        "images": len(shas),
        "computed": stats.computed,
        "cache_hits": stats.cache_hits,
        "resized": stats.resized,
        "rejected_corrupt_image": len(stats.failed),
        "max_side": config.max_side,
        "derived_max_side": stats.max_side_seen,
        "derived_mean_width": round(stats.width_sum / made, 1) if made else 0,
        "derived_mean_height": round(stats.height_sum / made, 1) if made else 0,
        "new_blob_bytes": stats.new_blob_bytes,
        "images_per_s": round(len(shas) / max(seconds, 1e-9), 1),
    }
    return PreprocessResult(record, stats.new_blob_bytes, metrics, seconds)
