"""Stratal command line.

  python -m stratal.cli ingest --dataset NAME --source /path [--min-size 256]
  python -m stratal.cli versions --dataset NAME
  python -m stratal.cli lineage VERSION_ID
  python -m stratal.cli models list
  python -m stratal.cli models pull NAME... | --all

Results are printed as JSON on standard output. Log lines go to standard error.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from stratal.core.errors import StratalError, ValidationFailed
from stratal.core.logging import configure_logging
from stratal.core.settings import get_settings
from stratal.data import registry
from stratal.data.ingest import IngestConfig, run_ingest
from stratal.data.versioning import derive_version_identity
from stratal.db.engine import get_engine
from stratal.db.models import DatasetVersion
from stratal.mon.sink import LogSink
from stratal.pretrained import catalog
from stratal.pretrained.pull import pull


def _version_json(version: DatasetVersion) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "stage": version.stage,
        "parent_version_id": version.parent_version_id,
        "row_count": version.row_count,
        "counts": version.counts,
        "config": version.config,
        "manifest_path": version.manifest_path,
        "manifest_bytes": version.manifest_bytes,
        "new_blob_bytes": version.new_blob_bytes,
        "created_at": version.created_at.isoformat(),
    }


def _ingest(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    result = run_ingest(
        source=Path(args.source),
        data_dir=settings.data_dir,
        config=IngestConfig(min_size=args.min_size),
        derive=derive_version_identity,
        sink=LogSink(),
    )
    with Session(get_engine()) as session, session.begin():
        dataset = registry.ensure_dataset(
            session, slug=args.dataset, modality="image", source_uri=str(args.source)
        )
        version, created = registry.register_version(
            session, dataset=dataset, record=result.record, new_blob_bytes=result.new_blob_bytes
        )
        return {**_version_json(version), "created": created, "seconds": result.seconds}


def _versions(args: argparse.Namespace) -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        return [_version_json(v) for v in registry.list_versions(session, args.dataset)]


def _lineage(args: argparse.Namespace) -> list[dict[str, Any]]:
    with Session(get_engine()) as session:
        return [_version_json(v) for v in registry.lineage(session, args.version_id)]


def _models_list(_args: argparse.Namespace) -> list[dict[str, Any]]:
    models_dir = get_settings().data_dir / "models"
    rows: list[dict[str, Any]] = []
    for model in catalog.CATALOG.values():
        try:
            folder: str | None = str(catalog.local_path(models_dir, model.name))
        except StratalError:
            folder = None
        rows.append(
            {
                "name": model.name,
                "repo_id": model.repo_id,
                "revision": model.revision,
                "role": model.role,
                "licence": model.licence,
                "size_mb": round(model.total_bytes / 1e6),
                "pulled": folder,
            }
        )
    return rows


def _models_pull(args: argparse.Namespace) -> list[dict[str, Any]]:
    names: list[str] = list(catalog.CATALOG) if args.all else list(args.names)
    if not names:
        raise ValidationFailed("Name at least one model, or pass --all.")
    models = [catalog.get_model(name) for name in names]  # refuse before downloading anything
    models_dir = get_settings().data_dir / "models"
    results: list[dict[str, Any]] = []
    for model in models:
        result = pull(model, models_dir, progress=lambda message: print(message, file=sys.stderr))
        results.append(
            {
                "name": result.name,
                "folder": str(result.folder),
                "files_downloaded": result.files_downloaded,
                "files_present": result.files_present,
                "downloaded_mb": round(result.downloaded_bytes / 1e6),
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stratal")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest a folder as a raw dataset version")
    ingest.add_argument("--dataset", required=True)
    ingest.add_argument("--source", required=True)
    ingest.add_argument("--min-size", type=int, default=IngestConfig().min_size)
    ingest.set_defaults(run=_ingest)

    versions = commands.add_parser("versions", help="list a dataset's versions, newest first")
    versions.add_argument("--dataset", required=True)
    versions.set_defaults(run=_versions)

    lineage = commands.add_parser("lineage", help="show a version's ancestry back to raw")
    lineage.add_argument("version_id")
    lineage.set_defaults(run=_lineage)

    models = commands.add_parser("models", help="the pretrained-model allow-list (NFR-8)")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    listing = model_commands.add_parser("list", help="allowed models and whether each is pulled")
    listing.set_defaults(run=_models_list)
    pulling = model_commands.add_parser("pull", help="download and verify allowed models")
    pulling.add_argument("names", nargs="*")
    pulling.add_argument("--all", action="store_true")
    pulling.set_defaults(run=_models_pull)

    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level, sys.stderr)
    try:
        output: Any = args.run(args)
    except StratalError as exc:
        problem = {"code": exc.code, "detail": exc.detail, "suggested_action": exc.suggested_action}
        print(json.dumps({"error": problem}, indent=2))
        return 1
    except NotImplementedError as exc:
        print(json.dumps({"error": {"code": "not_implemented", "detail": str(exc)}}, indent=2))
        return 2
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
