"""Datasets, versions, rows, blobs and sources (docs/API.md §3.2; DATA-3.1 to 3.4, UI-2.1)."""

import io
import os
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path as FsPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Response
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from stratal.api import schemas
from stratal.api.jobs import Db, Limit
from stratal.core.errors import Conflict, NotFound, ValidationFailed
from stratal.core.settings import get_settings
from stratal.data import browse, profile, registry
from stratal.db.models import Dataset, Derivation
from stratal.label.semantic import cosine, unpack_vector
from stratal.registry import store as model_store
from stratal.storage.blobs import BlobStore
from stratal.storage.derivations import KIND_CLIP_IMAGE
from stratal.storage.manifest import select_rows

router = APIRouter(tags=["datasets"])
Json = dict[str, Any]

SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,62}$"
SHA256 = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
IMMUTABLE = "public, max-age=31536000, immutable"
_MEDIA = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}


class CreateDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN, description="2–63 lowercase letters, digits, - or _.")
    display_name: str | None = Field(default=None, max_length=200)
    modality: Literal["image", "tabular", "text"]


def _offset(cursor: str | None) -> int:
    return int(cursor) if cursor and cursor.isdigit() else 0


def _page(items: list[Json], cursor: str | None, limit: int) -> Json:
    """Opaque cursor over a short list held in memory (datasets, versions of one dataset)."""
    start = _offset(cursor)
    end = start + limit
    return {"items": items[start:end], "next_cursor": str(end) if end < len(items) else None}


# -- datasets ------------------------------------------------------------------


@router.post("/datasets", status_code=201, response_model=schemas.Dataset)
def create_dataset(body: CreateDataset, session: Db) -> Json:
    """Create a dataset. ``409 conflict`` if the slug is taken."""
    with session.begin():
        if session.scalar(select(Dataset).where(Dataset.slug == body.slug)) is not None:
            raise Conflict(
                f"Dataset {body.slug!r} already exists.",
                suggested_action="Use the existing dataset, or choose another slug.",
            )
        dataset = Dataset(slug=body.slug, display_name=body.display_name, modality=body.modality)
        session.add(dataset)
        session.flush()
        view = browse.dataset_view(session, dataset)
    return view


@router.get("/datasets", response_model=schemas.DatasetList)
def list_datasets(session: Db, limit: Limit = 50, cursor: str | None = None) -> Json:
    """Datasets with their latest version, most recently updated first."""
    return _page(browse.list_datasets(session), cursor, limit)


@router.get("/datasets/{slug}", response_model=schemas.Dataset)
def get_dataset(slug: str, session: Db) -> Json:
    return browse.dataset_view(session, browse.get_dataset(session, slug))


@router.get("/datasets/{slug}/versions", response_model=schemas.VersionList)
def dataset_versions(slug: str, session: Db, limit: Limit = 50, cursor: str | None = None) -> Json:
    """A dataset's versions, newest first."""
    browse.get_dataset(session, slug)
    views = [browse.version_view(session, v, slug) for v in registry.list_versions(session, slug)]
    return _page(views, cursor, limit)


# -- versions ------------------------------------------------------------------


@router.get("/versions/{version_id}", response_model=schemas.Version)
def get_version(version_id: str, session: Db) -> Json:
    return browse.version_view(session, registry.get_version(session, version_id))


@router.get("/versions/{version_id}/lineage", response_model=schemas.Lineage)
def version_lineage(version_id: str, session: Db) -> Json:
    """Ancestors back to raw and descendants (DATA-3.3), plus jobs about to add a child."""
    return browse.lineage_view(session, version_id)


@router.get("/versions/{version_id}/rows", response_model=schemas.RowPage)
def version_rows(
    version_id: str,
    session: Db,
    status: Literal["accepted", "rejected", "duplicate", "filtered"] | None = None,
    reason_code: str | None = None,
    split: Literal["train", "val", "test"] | None = None,
    dup_group_id: str | None = None,
    semantic_cluster_id: str | None = None,
    semantic: Annotated[
        bool | None, Query(description="Only rows in a semantic cluster (DATA-2.3).")
    ] = None,
    q: Annotated[str | None, Query(max_length=200, description="Row-key substring.")] = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Json:
    """Manifest rows in ``row_key`` order. ``matched`` counts every row that passes the
    filters; ``next_cursor`` is the last row key of a full page."""
    _, path = registry.manifest_for(session, get_settings().data_dir, version_id)
    given = {
        "status": status,
        "reason_code": reason_code,
        "split": split,
        "dup_group_id": dup_group_id,
        "semantic_cluster_id": semantic_cluster_id,
    }
    page = select_rows(
        path,
        equals={k: v for k, v in given.items() if v is not None},
        key_contains=q,
        after_key=cursor,
        limit=limit,
        not_null=["semantic_cluster_id"] if semantic else [],
    )
    full = len(page.rows) == limit
    return {
        "items": page.rows,
        "matched": page.matched,
        "next_cursor": str(page.rows[-1]["row_key"]) if full else None,
    }


@router.get("/versions/{version_id}/stats", response_model=schemas.Stats)
def version_stats(version_id: str, session: Db) -> Json:
    """Counts by status, reason and deciding stage; size and CLIP-score distributions."""
    version, path = registry.manifest_for(session, get_settings().data_dir, version_id)
    return {"version_id": version_id, **browse.version_stats(path, version.row_count)}


# -- blobs and sources ---------------------------------------------------------


def _thumbnail(path: FsPath, width: int) -> bytes:
    with Image.open(path) as image:
        image.draft("RGB", (width, width))
        upright = ImageOps.exif_transpose(image).convert("RGB")
    if upright.width > width:
        height = max(1, round(upright.height * width / upright.width))
        upright = upright.resize((width, height), Image.Resampling.LANCZOS)  # pyright: ignore[reportUnknownMemberType]
    buffer = io.BytesIO()
    upright.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


@router.get("/blobs/{sha256}", response_class=Response)
def get_blob(
    sha256: SHA256,
    w: Annotated[int | None, Query(ge=16, le=2048, description="Thumbnail width.")] = None,
) -> Response:
    """A stored file by content hash. ``?w=`` returns an upright JPEG at most that wide.

    Blobs never change (DATA_MODEL.md §6), so responses are cached for good.
    """
    path = BlobStore(get_settings().data_dir).path_for(sha256)
    if not path.is_file():
        raise NotFound(f"No blob {sha256[:12]}… in the store.")
    headers = {"Cache-Control": IMMUTABLE, "ETag": f'"{sha256}{"-w" + str(w) if w else ""}"'}
    if w is not None:
        return Response(_thumbnail(path, w), media_type="image/jpeg", headers=headers)
    try:
        with Image.open(path) as image:
            media = _MEDIA.get(image.format or "", "application/octet-stream")
    except Exception:
        media = "application/octet-stream"
    return FileResponse(path, media_type=media, headers=headers)


def _file_count(folder: FsPath) -> int:
    count = 0
    for _root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        count += sum(1 for f in files if not f.startswith("."))
    return count


@router.get("/sources", response_model=schemas.SourceList)
def list_sources() -> Json:
    """Folders directly under the sources mount, with their file counts (SCR-02 drawer)."""
    root = get_settings().sources_dir
    folders = (
        sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        if root.is_dir()
        else []
    )
    items = [
        {"name": p.name, "path": f"{root.as_posix()}/{p.name}", "files": _file_count(p)}
        for p in folders
    ]
    return {"items": items, "next_cursor": None}


# -- what a version holds, how two differ, models trained on it (SCR-03 v2) ------------

_PROFILES: OrderedDict[tuple[str, ...], Json] = OrderedDict()
_PROFILE_KEEP = 16
"""Versions never change (K10), so their profiles are cached in this process by ID."""


def _cached(key: tuple[str, ...], build: Callable[[], Json]) -> Json:
    if key in _PROFILES:
        _PROFILES.move_to_end(key)
        return _PROFILES[key]
    value = build()
    _PROFILES[key] = value
    while len(_PROFILES) > _PROFILE_KEEP:
        _PROFILES.popitem(last=False)
    return value


def _steps(session: Db, version_id: str) -> list[profile.Step]:
    steps: list[profile.Step] = []
    current: str | None = version_id
    while current is not None:
        version = registry.get_version(session, current)
        steps.append(profile.Step(version.version_id, version.stage, dict(version.config or {})))
        current = version.parent_version_id
    return steps[::-1]


@router.get("/versions/{version_id}/distributions", response_model=schemas.Distributions)
def version_distributions(version_id: str, session: Db) -> Json:
    """The funnel from raw to this version, then each column raw (every row) against here
    (accepted rows) and the removed rows (UI-2.2, K10)."""
    version, path = registry.manifest_for(session, get_settings().data_dir, version_id)
    steps = _steps(session, version_id)

    def build() -> Json:
        rows = profile.read_all(path)
        return {
            "version_id": version_id,
            "stage": version.stage,
            "dtype": version.dtype,
            **profile.distributions(rows, dtype=version.dtype, lineage=steps),
        }

    return _cached(("dist", version_id), build)


@router.get("/versions/{version_id}/diff", response_model=schemas.VersionDiff)
def version_diff(
    version_id: str,
    session: Db,
    with_: Annotated[str, Query(alias="with", description="The other version, A.")],
) -> Json:
    """This version (B) against another version of the same dataset (A), rows matched by
    ``row_key`` (DATA-3.5, K10). Any two versions of one dataset can be compared; when
    their stages differ, ``stages_differ`` says so."""
    data_dir = get_settings().data_dir
    b_version, b_path = registry.manifest_for(session, data_dir, version_id)
    a_version, a_path = registry.manifest_for(session, data_dir, with_)
    if a_version.dataset_id != b_version.dataset_id:
        raise ValidationFailed(
            f"{with_} and {version_id} belong to different datasets.",
            suggested_action="Compare two versions of one dataset.",
            errors=[
                {"loc": ["query", "with"], "msg": "another dataset's version", "code": "dataset"}
            ],
        )
    a_steps, b_steps = _steps(session, with_), _steps(session, version_id)

    def build() -> Json:
        body = profile.diff(
            profile.read_all(a_path), profile.read_all(b_path), a_steps=a_steps, b_steps=b_steps
        )
        return {
            "a": {
                "version_id": with_,
                "stage": a_version.stage,
                "created_at": browse.iso(a_version.created_at),
            },
            "b": {
                "version_id": version_id,
                "stage": b_version.stage,
                "created_at": browse.iso(b_version.created_at),
            },
            **body,
        }

    return _cached(("diff", with_, version_id), build)


@router.get("/versions/{version_id}/models", response_model=schemas.VersionModels)
def version_models(version_id: str, session: Db) -> Json:
    """Model versions trained on this version, by lineage, with their main metric and the
    split it is on: test once scored, else validation (UI-2.1, SCR-03 v2 frame D)."""
    registry.get_version(session, version_id)
    return {"items": model_store.trained_on(session, version_id)}


@router.get(
    "/versions/{version_id}/clusters/{cluster_id}", response_model=schemas.SemanticClusterView
)
def semantic_cluster(version_id: str, cluster_id: str, session: Db) -> Json:
    """A semantic cluster's members, the CLIP similarity and pHash distance of each pair,
    and their split (DATA-2.3, DATA-4.8; SCR-03 v2 frame C)."""
    _, path = registry.manifest_for(session, get_settings().data_dir, version_id)
    page = select_rows(path, equals={"semantic_cluster_id": cluster_id}, limit=200)
    if not page.rows:
        raise NotFound(f"No semantic cluster {cluster_id!r} in {version_id}.")
    vectors = _vectors(
        session, [str(r["derived_sha256"]) for r in page.rows if r.get("derived_sha256")]
    )
    pairs: list[Json] = []
    for i, a in enumerate(page.rows):
        for b in page.rows[i + 1 :]:
            va, vb = (
                vectors.get(str(a.get("derived_sha256"))),
                vectors.get(str(b.get("derived_sha256"))),
            )
            phash = None
            if a.get("phash") and b.get("phash"):
                phash = (int(str(a["phash"]), 16) ^ int(str(b["phash"]), 16)).bit_count()
            pairs.append(
                {
                    "a": a["row_key"],
                    "b": b["row_key"],
                    "similarity": None if va is None or vb is None else round(cosine(va, vb), 4),
                    "phash_distance": phash,
                }
            )
    return {
        "cluster_id": cluster_id,
        "rows": page.rows,
        "pairs": pairs,
        "split_groups": sorted({str(r["split_group"]) for r in page.rows if r.get("split_group")}),
    }


def _vectors(session: Db, shas: list[str]) -> dict[str, list[float]]:
    """The cached CLIP vectors of these preprocessed copies, whatever the library versions
    they were computed under (the API cannot know the GPU worker's)."""
    found: dict[str, list[float]] = {}
    for row in session.scalars(
        select(Derivation).where(
            Derivation.kind == KIND_CLIP_IMAGE, Derivation.input_hash.in_(shas)
        )
    ):
        found.setdefault(row.input_hash, unpack_vector(str(row.value)))
    return found
