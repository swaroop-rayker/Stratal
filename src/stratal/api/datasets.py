"""Datasets, versions, rows, blobs and sources (docs/API.md §3.2; DATA-3.1 to 3.4, UI-2.1)."""

import io
import os
from pathlib import Path as FsPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Response
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from stratal.api.jobs import Db, Limit
from stratal.core.errors import Conflict, NotFound
from stratal.core.settings import get_settings
from stratal.data import browse, registry
from stratal.db.models import Dataset
from stratal.storage.blobs import BlobStore
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


@router.post("/datasets", status_code=201)
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


@router.get("/datasets")
def list_datasets(session: Db, limit: Limit = 50, cursor: str | None = None) -> Json:
    """Datasets with their latest version, most recently updated first."""
    return _page(browse.list_datasets(session), cursor, limit)


@router.get("/datasets/{slug}")
def get_dataset(slug: str, session: Db) -> Json:
    return browse.dataset_view(session, browse.get_dataset(session, slug))


@router.get("/datasets/{slug}/versions")
def dataset_versions(slug: str, session: Db, limit: Limit = 50, cursor: str | None = None) -> Json:
    """A dataset's versions, newest first."""
    browse.get_dataset(session, slug)
    views = [browse.version_view(session, v, slug) for v in registry.list_versions(session, slug)]
    return _page(views, cursor, limit)


# -- versions ------------------------------------------------------------------


@router.get("/versions/{version_id}")
def get_version(version_id: str, session: Db) -> Json:
    return browse.version_view(session, registry.get_version(session, version_id))


@router.get("/versions/{version_id}/lineage")
def version_lineage(version_id: str, session: Db) -> Json:
    """Ancestors back to raw and descendants (DATA-3.3), plus jobs about to add a child."""
    return browse.lineage_view(session, version_id)


@router.get("/versions/{version_id}/rows")
def version_rows(
    version_id: str,
    session: Db,
    status: Literal["accepted", "rejected", "duplicate", "filtered"] | None = None,
    reason_code: str | None = None,
    split: Literal["train", "val", "test"] | None = None,
    dup_group_id: str | None = None,
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
    }
    page = select_rows(
        path,
        equals={k: v for k, v in given.items() if v is not None},
        key_contains=q,
        after_key=cursor,
        limit=limit,
    )
    full = len(page.rows) == limit
    return {
        "items": page.rows,
        "matched": page.matched,
        "next_cursor": str(page.rows[-1]["row_key"]) if full else None,
    }


@router.get("/versions/{version_id}/stats")
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


@router.get("/sources")
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
