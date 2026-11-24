"""Stage 3: caption every accepted image, then score each pair with CLIP (LABEL-1.1 to 1.3).

The shell around the owner's units (ADR-0012). It reads the preprocessed copies
(DATA-6.1), finds what is already in the derivation cache, and only for what is
missing takes the GPU lease (BUG6), caps VRAM (ADR-0006), loads one model at a
time through the owner's :class:`~stratal.label.vram.VramManager`, and calls:

1. :func:`~stratal.label.captioning.caption_items` with the uncaptioned images.
   Each finished batch is committed to the cache, so a killed stage resumes
   where it stopped (LABEL-1.2).
2. :func:`~stratal.label.scoring.clip_similarity` on CLIP's embeddings of each
   image and its caption, batch by batch, also committed per batch.

Then a second pass writes the manifest with ``caption``, ``caption_model``,
``caption_model_revision``, ``clip_score``, ``clip_model`` and
``clip_model_revision`` on every accepted row (LABEL-1.1.AC1, LABEL-1.3.AC1).
Identical copies are captioned once. Nothing here imports PyTorch: the models
come through :class:`LabelModels`, built from :mod:`stratal.label.hf` in the
GPU worker and from fakes in tests.
"""

import hashlib
import math
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from stratal.core.errors import GpuOom, StratalError
from stratal.data.commit import VersionRecord, commit_version
from stratal.data.versioning import VersionDeriver
from stratal.label import captioning, scoring, semantic
from stratal.label.captioning import CaptionItem, CaptionReport
from stratal.label.models import Captioner, ClipEncoder, CudaOutOfMemory, GpuBackend, GpuModel
from stratal.label.vram import UnloadReport, VramManager
from stratal.mon.events import MetricValue
from stratal.mon.sink import EventSink
from stratal.pretrained.catalog import get_model
from stratal.storage.blobs import BlobStore
from stratal.storage.derivations import (
    KIND_CAPTION,
    KIND_CLIP_IMAGE,
    KIND_CLIP_SCORE,
    DerivationCache,
    params_hash,
)
from stratal.storage.manifest import STATUS_ACCEPTED, STATUS_DUPLICATE, Row, iter_rows

STAGE = "label"
_LOOKUP_BATCH = 1000
_ROW_BATCH = 2000


class LabelConfig(BaseModel):
    """Settings for the label stage. Every field is recorded in the version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    captioner: Literal["florence-2-base", "blip-large"] = Field(
        default="florence-2-base",
        description="Captioning model from the allow-list (LABEL-1.1; Florence-2 after S-06).",
    )
    caption_task: Literal["<CAPTION>", "<DETAILED_CAPTION>", "<MORE_DETAILED_CAPTION>"] = Field(
        default="<CAPTION>", description="Florence-2 task prompt; ignored by BLIP."
    )
    max_new_tokens: int = Field(default=40, ge=1, le=512)
    num_beams: int = Field(default=3, ge=1, le=8)
    caption_batch_size: int = Field(default=16, ge=1, le=256)
    clip_model: Literal["clip-vit-b-32-laion2b"] = Field(
        default="clip-vit-b-32-laion2b", description="CLIP model for LABEL-1.3."
    )
    clip_batch_size: int = Field(default=64, ge=1, le=1024)
    dtype: Literal["fp16", "fp32"] = "fp16"
    vram_fraction: float = Field(
        default=0.85, gt=0, le=0.95, description="PyTorch allocator cap (ADR-0006 rule 8)."
    )
    caption_vram_estimate_mb: int = Field(
        default=3900,
        ge=1,
        description="Working VRAM the captioner needs beyond its weights file. Florence-2 at "
        "batch 16 peaked at 4.05 GB reserved in S-06 (3.78 GB in the NFR-1 run), weights "
        "included, so 0.44 + 3.9 GB leaves about 0.25 GB of margin.",
    )
    clip_vram_estimate_mb: int = Field(default=1200, ge=1)
    caption_algorithm_version: int = Field(
        default=captioning.ALGORITHM_VERSION, json_schema_extra={"readOnly": True}
    )
    scoring_algorithm_version: int = Field(
        default=scoring.ALGORITHM_VERSION, json_schema_extra={"readOnly": True}
    )
    semantic_threshold: float = Field(
        default=semantic.DEFAULT_THRESHOLD,
        ge=0.5,
        le=1.0,
        description="Images whose CLIP vectors are at least this alike (cosine) form a "
        "semantic cluster (DATA-2.3). 0.90 from spike S-10; use 0.95 or more to remove.",
    )
    semantic_action: Literal["mark", "remove"] = Field(
        default="mark",
        description="mark: every member stays accepted and shares semantic_cluster_id, which "
        "keeps a cluster in one split group (DATA-4.8). remove: all but one per cluster become "
        "duplicates (ADR-0013 rule 3).",
    )
    semantic_algorithm_version: int = Field(
        default=semantic.ALGORITHM_VERSION, json_schema_extra={"readOnly": True}
    )


class CaptionLoop(Protocol):
    def __call__(
        self,
        items: Sequence[CaptionItem],
        *,
        captioner: Captioner,
        batch_size: int,
        open_image: Callable[[CaptionItem], Image.Image],
        commit: Callable[[Mapping[str, str]], None],
        sink: EventSink,
        checkpoint: Callable[[], None],
        stage: str = STAGE,
    ) -> CaptionReport: ...


class Similarity(Protocol):
    def __call__(
        self, image_vectors: Sequence[list[float]], text_vectors: Sequence[list[float]]
    ) -> list[float]: ...


class ModelManager(Protocol):
    def load[M: GpuModel](self, loader: Callable[[], M], *, name: str, estimate_mb: float) -> M: ...

    def unload(self) -> UnloadReport: ...


@dataclass(frozen=True)
class LabelUnits:
    """The owner's units; tests pass fakes."""

    caption: CaptionLoop = captioning.caption_items
    similarity: Similarity = scoring.clip_similarity
    clusters: Callable[..., list[semantic.SemanticCluster]] = semantic.semantic_clusters
    vram: Callable[[GpuBackend, EventSink], ModelManager] = lambda gpu, sink: VramManager(
        gpu, sink=sink, stage=STAGE
    )


OWNER_UNITS = LabelUnits()


@dataclass(frozen=True)
class LabelModels:
    """Where the GPU and the models come from: :mod:`stratal.label.hf`, or fakes."""

    gpu: Callable[[], GpuBackend]
    load_captioner: Callable[[LabelConfig], Captioner]
    load_clip: Callable[[LabelConfig], ClipEncoder]
    prepare: Callable[[LabelConfig], None] = lambda _config: None
    """Called once the lease is held, before any model loads (sets the VRAM cap)."""
    library_versions: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    """Library versions that can change outputs; part of the cache keys."""


LeaseFactory = Callable[[], AbstractContextManager[object]]


@dataclass(frozen=True)
class LabelResult:
    record: VersionRecord
    metrics: dict[str, MetricValue]
    seconds: float


def caption_params(config: LabelConfig, versions: Mapping[str, str]) -> dict[str, Any]:
    model = get_model(config.captioner)
    return {
        "kind": KIND_CAPTION,
        "model": model.name,
        "revision": model.revision,
        "task": config.caption_task if model.name.startswith("florence") else None,
        "max_new_tokens": config.max_new_tokens,
        "num_beams": config.num_beams,
        "dtype": config.dtype,
        "algorithm": config.caption_algorithm_version,
        "libraries": dict(sorted(versions.items())),
    }


def clip_params(config: LabelConfig, versions: Mapping[str, str]) -> dict[str, Any]:
    model = get_model(config.clip_model)
    return {
        "kind": KIND_CLIP_SCORE,
        "model": model.name,
        "revision": model.revision,
        "dtype": config.dtype,
        "algorithm": config.scoring_algorithm_version,
        "libraries": dict(sorted(versions.items())),
    }


def embedding_params(config: LabelConfig, versions: Mapping[str, str]) -> dict[str, Any]:
    """An image vector depends on the CLIP model and the libraries, not on the caption."""
    model = get_model(config.clip_model)
    return {
        "model": model.name,
        "revision": model.revision,
        "dtype": config.dtype,
        "libraries": dict(sorted(versions.items())),
    }


def pair_key(image_sha256: str, caption: str) -> str:
    """Cache key of one image-caption pair."""
    return hashlib.sha256(f"{image_sha256}\n{caption}".encode()).hexdigest()


def _estimate(name: str, working_mb: int) -> float:
    """VRAM to have free before loading: the weights file's size plus working memory.

    The file size is an upper bound for the weights at either precision: some
    checkpoints are stored in fp16 (Florence-2-base, 452 MB on the card) and
    some in fp32 (CLIP, BLIP), which loading in fp16 halves.
    """
    return get_model(name).weights_bytes / 2**20 + working_mb


@dataclass
class _Stats:
    images: int = 0
    caption_hits: int = 0
    clip_hits: int = 0
    report: CaptionReport | None = None
    clip_computed: int = 0
    score_sum: float = 0.0
    scored_rows: int = 0
    row_score_sum: float = 0.0
    used_gpu: bool = False
    embeddings_computed: int = 0
    clusters: list[semantic.SemanticCluster] = field(
        default_factory=lambda: list[semantic.SemanticCluster]()
    )


class _Labeler:
    def __init__(
        self,
        *,
        data_dir: Path,
        config: LabelConfig,
        sink: EventSink,
        cache: DerivationCache,
        models: LabelModels,
        units: LabelUnits,
        lease: LeaseFactory,
        checkpoint: Callable[[], None],
    ) -> None:
        self.blobs = BlobStore(data_dir)
        self.config = config
        self.sink = sink
        self.cache = cache
        self.models = models
        self.units = units
        self.lease = lease
        self.checkpoint = checkpoint
        self.caption_params = params_hash(caption_params(config, models.library_versions))
        self.clip_params = params_hash(clip_params(config, models.library_versions))
        self.embedding_params = params_hash(embedding_params(config, models.library_versions))
        self.cluster_of: dict[str, str] = {}
        """A row's original ``sha256`` → its cluster's ID (DATA-2.3)."""
        self.key_of_sha: dict[str, str] = {}
        self.captioner = get_model(config.captioner)
        self.clip = get_model(config.clip_model)
        self.stats = _Stats()

    def _lookup(self, kind: str, params: str, keys: Sequence[str]) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for start in range(0, len(keys), _LOOKUP_BATCH):
            found.update(self.cache.get_many(kind, params, keys[start : start + _LOOKUP_BATCH]))
        return found

    def _open(self, sha: str) -> Image.Image:
        path = self.blobs.path_for(sha)
        if not path.is_file():
            raise StratalError(
                f"The preprocessed copy {sha[:12]}… is missing from the data volume.",
                suggested_action="Rerun preprocess for this version, or check the volume.",
            )
        with Image.open(path) as image:
            return image.convert("RGB")

    def work(self, images: list[str]) -> None:
        """Pass 1: caption and score whatever the cache does not have yet."""
        self.stats.images = len(images)
        cached = self._lookup(KIND_CAPTION, self.caption_params, images)
        self.stats.caption_hits = len(cached)
        todo = [CaptionItem(key=sha, row_key=sha[:12]) for sha in images if sha not in cached]
        self.sink.emit(
            stage=STAGE,
            message=f"{len(images):,} images to label: {len(todo):,} need captions, "
            f"{len(cached):,} already captioned",
            metrics={"images": len(images), "caption_cache_hits": len(cached)},
        )
        if todo:
            with self.lease():
                self.stats.used_gpu = True
                self.models.prepare(self.config)
                self._caption(todo)
        captions = self._captions(images)
        pairs = {pair_key(sha, caption): sha for sha, caption in captions.items()}
        scored = self._lookup(KIND_CLIP_SCORE, self.clip_params, sorted(pairs))
        self.stats.clip_hits = len(scored)
        missing = [
            (key, pairs[key], captions[pairs[key]]) for key in sorted(pairs) if key not in scored
        ]
        if missing:
            with self.lease():
                self.stats.used_gpu = True
                self.models.prepare(self.config)
                self._score(missing)
        vectors = self._lookup(KIND_CLIP_IMAGE, self.embedding_params, images)
        unembedded = [sha for sha in images if sha not in vectors]
        if unembedded:
            with self.lease():
                self.stats.used_gpu = True
                self.models.prepare(self.config)
                self._embed(unembedded)

    def cluster(self, accepted: list[tuple[str, str, str]]) -> None:
        """Semantic clusters of the accepted rows (DATA-2.3, ADR-0013 rule 1). ``accepted``
        holds ``(row_key, sha256, derived_sha256)``."""
        derived = sorted({d for _k, _s, d in accepted})
        found = self._lookup(KIND_CLIP_IMAGE, self.embedding_params, derived)
        items = [
            semantic.EmbeddedItem(sha, semantic.unpack_vector(str(found[d])))
            for _key, sha, d in accepted
            if d in found
        ]
        self.sink.emit(
            stage=STAGE,
            substage="semantic",
            message=f"Comparing {len(items):,} CLIP vectors at "
            f"{self.config.semantic_threshold:.2f}",
        )
        started = time.monotonic()
        clusters = self.units.clusters(items, threshold=self.config.semantic_threshold)
        self.stats.clusters = clusters
        self.cluster_of = {sha: c.cluster_id for c in clusters for sha in c.members}
        self.key_of_sha = {sha: key for key, sha, _d in accepted}
        rows = sum(len(c.members) for c in clusters)
        self.sink.emit(
            stage=STAGE,
            substage="semantic",
            message=f"{len(clusters):,} semantic clusters, {rows:,} rows in them"
            + (f", the largest {max(len(c.members) for c in clusters)}" if clusters else ""),
            metrics={
                "semantic_clusters": len(clusters),
                "semantic_rows": rows,
                "semantic_seconds": round(time.monotonic() - started, 2),
            },
        )

    def _embed(self, images: list[str]) -> None:
        """Image vectors for images scored before M11, whose vectors were not kept."""
        manager = self.units.vram(self.models.gpu(), self.sink)
        clip = manager.load(
            lambda: self.models.load_clip(self.config),
            name=self.clip.display,
            estimate_mb=_estimate(self.clip.name, self.config.clip_vram_estimate_mb),
        )
        size = self.config.clip_batch_size
        total = math.ceil(len(images) / size)
        try:
            for index, start in enumerate(range(0, len(images), size), start=1):
                self.checkpoint()
                batch = images[start : start + size]
                try:
                    vectors = clip.encode_images([self._open(sha) for sha in batch])
                except CudaOutOfMemory as exc:
                    raise GpuOom(
                        f"CLIP ran out of VRAM on a batch of {len(batch)}: {exc.detail}",
                        suggested_action="Set stages.label.clip_batch_size to "
                        f"{max(1, size // 2)}.",
                    ) from None
                self.cache.put_many(
                    KIND_CLIP_IMAGE,
                    self.embedding_params,
                    {sha: semantic.pack_vector(v) for sha, v in zip(batch, vectors, strict=True)},
                )
                self.stats.embeddings_computed += len(batch)
                self.sink.emit(
                    stage=STAGE,
                    substage="clip",
                    message=f"CLIP image vectors {index}/{total} · {self.clip.display} · bs={size}",
                    step=index,
                    total=total,
                )
        finally:
            del clip
            manager.unload()

    def _caption(self, todo: list[CaptionItem]) -> None:
        manager = self.units.vram(self.models.gpu(), self.sink)
        captioner = manager.load(
            lambda: self.models.load_captioner(self.config),
            name=self.captioner.display,
            estimate_mb=_estimate(self.captioner.name, self.config.caption_vram_estimate_mb),
        )
        try:
            self.stats.report = self.units.caption(
                todo,
                captioner=captioner,
                batch_size=self.config.caption_batch_size,
                open_image=lambda item: self._open(item.key),
                commit=lambda done: self.cache.put_many(KIND_CAPTION, self.caption_params, done),
                sink=self.sink,
                checkpoint=self.checkpoint,
                stage=STAGE,
            )
        finally:
            del captioner
            manager.unload()

    def _captions(self, images: list[str]) -> dict[str, str]:
        found = self._lookup(KIND_CAPTION, self.caption_params, images)
        missing = [sha for sha in images if not isinstance(found.get(sha), str)]
        if missing:
            raise StratalError(
                f"{len(missing)} images have no caption after captioning, e.g. {missing[0][:12]}….",
                suggested_action="The caption loop must commit every image it is given "
                "(LABEL-1.1). Retry; if it repeats, check the owner's caption_items.",
            )
        return {sha: str(found[sha]) for sha in images}

    def _score(self, missing: list[tuple[str, str, str]]) -> None:
        manager = self.units.vram(self.models.gpu(), self.sink)
        clip = manager.load(
            lambda: self.models.load_clip(self.config),
            name=self.clip.display,
            estimate_mb=_estimate(self.clip.name, self.config.clip_vram_estimate_mb),
        )
        size = self.config.clip_batch_size
        total = math.ceil(len(missing) / size)
        started = time.monotonic()
        try:
            for index, start in enumerate(range(0, len(missing), size), start=1):
                self.checkpoint()
                batch = missing[start : start + size]
                try:
                    image_vectors = clip.encode_images([self._open(sha) for _key, sha, _c in batch])
                    text_vectors = clip.encode_texts([caption for _key, _sha, caption in batch])
                except CudaOutOfMemory as exc:
                    raise GpuOom(
                        f"CLIP ran out of VRAM on a batch of {len(batch)}: {exc.detail}",
                        suggested_action="Set stages.label.clip_batch_size to "
                        f"{max(1, size // 2)}.",
                    ) from None
                scores = self.units.similarity(image_vectors, text_vectors)
                if len(scores) != len(batch) or not all(math.isfinite(s) for s in scores):
                    raise StratalError(
                        f"clip_similarity returned {len(scores)} scores for {len(batch)} pairs, "
                        "or a score that is not finite.",
                        suggested_action="Check the owner's clip_similarity (LABEL-1.3).",
                    )
                self.cache.put_many(
                    KIND_CLIP_SCORE,
                    self.clip_params,
                    {key: float(s) for (key, _sha, _c), s in zip(batch, scores, strict=True)},
                )
                self.cache.put_many(  # kept for the semantic clusters (ADR-0013 rule 2)
                    KIND_CLIP_IMAGE,
                    self.embedding_params,
                    {
                        sha: semantic.pack_vector(v)
                        for (_key, sha, _c), v in zip(batch, image_vectors, strict=True)
                    },
                )
                self.stats.clip_computed += len(batch)
                self.stats.score_sum += sum(scores)
                rate = self.stats.clip_computed / max(time.monotonic() - started, 1e-9)
                self.sink.emit(
                    stage=STAGE,
                    substage="clip",
                    message=f"CLIP scoring batch {index}/{total} · {self.clip.display} · bs={size}",
                    step=index,
                    total=total,
                    metrics={
                        "images_per_s": round(rate, 1),
                        "clip_score_running_mean": round(
                            self.stats.score_sum / self.stats.clip_computed, 4
                        ),
                    },
                )
        finally:
            del clip
            manager.unload()

    def rows(self, parent_rows: Iterator[Row]) -> Iterator[Row]:
        """Pass 2: the parent's rows with captions and scores from the cache."""
        batch: list[Row] = []
        for row in parent_rows:
            batch.append(row)
            if len(batch) >= _ROW_BATCH:
                yield from self._fill(batch)
                batch = []
        yield from self._fill(batch)

    def _fill(self, batch: list[Row]) -> Iterator[Row]:
        accepted = [r for r in batch if r["status"] == STATUS_ACCEPTED]
        shas = sorted({str(r["derived_sha256"]) for r in accepted})
        captions = self._lookup(KIND_CAPTION, self.caption_params, shas)
        keys = {sha: pair_key(sha, str(captions[sha])) for sha in shas if sha in captions}
        scores = self._lookup(KIND_CLIP_SCORE, self.clip_params, sorted(keys.values()))
        for row in batch:
            if row["status"] == STATUS_ACCEPTED:
                sha = str(row["derived_sha256"])
                score = scores.get(keys.get(sha, ""))
                if sha not in captions or score is None:
                    raise StratalError(
                        f"Row {row['row_key']!r} lost its caption or score between the two passes.",
                        suggested_action="Retry the job; if it happens again, check the database.",
                    )
                row.update(
                    caption=str(captions[sha]),
                    caption_model=self.captioner.name,
                    caption_model_revision=self.captioner.revision,
                    clip_score=float(score),
                    clip_model=self.clip.name,
                    clip_model_revision=self.clip.revision,
                )
                self.stats.scored_rows += 1
                self.stats.row_score_sum += float(score)
                self._mark(row)
            yield row

    def _mark(self, row: Row) -> None:
        """``semantic_cluster_id`` on a member; with ``remove``, all but the cluster's
        first member become duplicates of it (ADR-0013 rule 3)."""
        cluster = self.cluster_of.get(str(row["sha256"]))
        if cluster is None:
            return
        row["semantic_cluster_id"] = cluster
        if self.config.semantic_action == "remove" and row["sha256"] != cluster:
            row.update(
                status=STATUS_DUPLICATE,
                decided_at_stage=STAGE,
                reason_code="semantic_duplicate",
                reason_detail=f"CLIP vector at least {self.config.semantic_threshold:.2f} alike "
                f"to {self.key_of_sha.get(cluster, cluster[:12])}",
                dup_kind="semantic",
                dup_group_id=cluster,
                dup_of=self.key_of_sha.get(cluster),
            )

    def semantic_counts(self) -> dict[str, Any]:
        """For the version's counts (ADR-0013 rule 5): the clusters and up to 20 of the
        largest, for SCR-03's tile."""
        clusters = self.stats.clusters
        largest = sorted(clusters, key=lambda c: (-len(c.members), c.cluster_id))[:20]
        return {
            "clusters": len(clusters),
            "rows_in_clusters": sum(len(c.members) for c in clusters),
            "largest": max((len(c.members) for c in clusters), default=0),
            "threshold": self.config.semantic_threshold,
            "action": self.config.semantic_action,
            "examples": [{"cluster_id": c.cluster_id, "rows": len(c.members)} for c in largest],
        }


def _accepted_rows(parent_manifest: Path) -> list[tuple[str, str, str]]:
    """``(row_key, sha256, derived_sha256)`` of every accepted row."""
    rows: list[tuple[str, str, str]] = []
    columns = ["row_key", "status", "sha256", "derived_sha256"]
    for row in iter_rows(parent_manifest, columns=columns):
        if row["status"] != STATUS_ACCEPTED:
            continue
        if not row["derived_sha256"]:
            raise StratalError(
                f"Accepted row {row['row_key']!r} has no preprocessed copy.",
                suggested_action="Label a version made by the preprocess stage.",
            )
        rows.append((str(row["row_key"]), str(row["sha256"]), str(row["derived_sha256"])))
    return rows


def run_label(
    *,
    parent_manifest: Path,
    parent_version_id: str,
    data_dir: Path,
    config: LabelConfig,
    derive: VersionDeriver,
    sink: EventSink,
    cache: DerivationCache,
    models: LabelModels,
    units: LabelUnits = OWNER_UNITS,
    lease: LeaseFactory = nullcontext,
    checkpoint: Callable[[], None] = lambda: None,
) -> LabelResult:
    """Caption and score the parent version and commit the result as a ``label`` version."""
    started = time.monotonic()
    labeler = _Labeler(
        data_dir=data_dir,
        config=config,
        sink=sink,
        cache=cache,
        models=models,
        units=units,
        lease=lease,
        checkpoint=checkpoint,
    )
    accepted = _accepted_rows(parent_manifest)
    images = sorted({derived for _key, _sha, derived in accepted})
    labeler.work(images)
    checkpoint()
    labeler.cluster(accepted)
    checkpoint()
    record = commit_version(
        data_dir=data_dir,
        stage=STAGE,
        parent_version_id=parent_version_id,
        rows=labeler.rows(iter_rows(parent_manifest)),
        config=config.model_dump(mode="json"),
        derive=derive,
        sink=sink,
    )
    record = replace(record, counts={**record.counts, "semantic": labeler.semantic_counts()})
    stats = labeler.stats
    seconds = round(time.monotonic() - started, 3)
    report = stats.report
    metrics: dict[str, MetricValue] = {
        "images": stats.images,
        "rows_labelled": stats.scored_rows,
        "captions_computed": report.captioned if report else 0,
        "caption_cache_hits": stats.caption_hits,
        "caption_batches": report.batches if report else 0,
        "final_batch_size": report.final_batch_size if report else config.caption_batch_size,
        "oom_retries": report.oom_retries if report else 0,
        "clip_computed": stats.clip_computed,
        "clip_cache_hits": stats.clip_hits,
        "embeddings_computed": stats.embeddings_computed,
        "semantic_clusters": len(stats.clusters),
        "semantic_rows": sum(len(c.members) for c in stats.clusters),
        "used_gpu": int(stats.used_gpu),
        "images_per_s": round(stats.images / max(seconds, 1e-9), 1),
    }
    if stats.scored_rows:
        metrics["clip_score_mean"] = round(stats.row_score_sum / stats.scored_rows, 4)
    return LabelResult(record, metrics, seconds)
