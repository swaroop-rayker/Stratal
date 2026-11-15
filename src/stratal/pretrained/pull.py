"""The explicit pull command: download an allowed model and verify every file (NFR-8).

This is the only code that contacts the model hub. Jobs load models from
:func:`stratal.pretrained.catalog.local_path` with the hub switched off (NFR-7).

Each file is streamed to a temporary name next to its target while its hashes
are computed, checked against the size and hash pinned in the catalog, and only
then renamed into place. A file that fails is deleted and the pull stops with
:class:`ModelIntegrityFailed`. A file already present and correct is kept, so an
interrupted pull resumes file by file. The stamp is written last.
"""

import hashlib
import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from stratal.core.errors import ModelIntegrityFailed
from stratal.pretrained.catalog import STAMP, ModelFile, PretrainedModel, model_dir

HUB = "https://huggingface.co"
CHUNK = 1 << 20

Opener = Callable[[str], BinaryIO]
Progress = Callable[[str], None]


def file_url(model: PretrainedModel, file: ModelFile) -> str:
    return f"{HUB}/{model.repo_id}/resolve/{model.revision}/{file.path}"


def _open_url(url: str) -> BinaryIO:
    request = urllib.request.Request(url, headers={"User-Agent": "stratal-model-pull"})
    return urllib.request.urlopen(request, timeout=60)


@dataclass(frozen=True)
class PullResult:
    name: str
    folder: Path
    downloaded_bytes: int
    files_downloaded: int
    files_present: int


class _Hashes:
    def __init__(self, size: int) -> None:
        self.sha256 = hashlib.sha256()
        self.git = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
        self.size = 0

    def update(self, chunk: bytes) -> None:
        self.sha256.update(chunk)
        self.git.update(chunk)
        self.size += len(chunk)


def _problem(file: ModelFile, hashes: _Hashes) -> str | None:
    """Why the content does not match the pin, or ``None`` if it does."""
    if hashes.size != file.size:
        return f"size {hashes.size} instead of {file.size}"
    if file.sha256 is not None and hashes.sha256.hexdigest() != file.sha256:
        return f"SHA-256 {hashes.sha256.hexdigest()} instead of {file.sha256}"
    if file.git_oid is not None and hashes.git.hexdigest() != file.git_oid:
        return f"git blob hash {hashes.git.hexdigest()} instead of {file.git_oid}"
    if file.sha256 is None and file.git_oid is None:
        return "no pinned hash in the catalog"
    return None


def verify_file(path: Path, file: ModelFile) -> bool:
    """Whether the file on disk matches its pin."""
    if not path.is_file() or path.stat().st_size != file.size:
        return False
    hashes = _Hashes(file.size)
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            hashes.update(chunk)
    return _problem(file, hashes) is None


def _download(url: str, target: Path, file: ModelFile, opener: Opener) -> None:
    partial = target.with_name(target.name + ".partial")
    hashes = _Hashes(file.size)
    try:
        with opener(url) as response, partial.open("wb") as out:
            while chunk := response.read(CHUNK):
                if hashes.size + len(chunk) > file.size:
                    raise ModelIntegrityFailed(
                        f"{file.path} is larger than its pinned {file.size} bytes.",
                        suggested_action="Check the catalog entry; do not use this file.",
                    )
                hashes.update(chunk)
                out.write(chunk)
        problem = _problem(file, hashes)
        if problem is not None:
            raise ModelIntegrityFailed(
                f"{file.path} failed its integrity check: {problem}.",
                suggested_action="Pull again; if it fails again, the source changed and the "
                "catalog entry needs review.",
            )
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def pull(
    model: PretrainedModel,
    models_dir: Path,
    *,
    opener: Opener = _open_url,
    progress: Progress = lambda _message: None,
) -> PullResult:
    folder = model_dir(models_dir, model)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STAMP).unlink(missing_ok=True)  # until every file is verified again
    downloaded = present = 0
    total = 0
    for file in model.files:
        target = folder / file.path
        if verify_file(target, file):
            present += 1
            continue
        target.unlink(missing_ok=True)
        progress(f"Downloading {model.name}/{file.path} ({file.size / 1e6:.1f} MB)")
        _download(file_url(model, file), target, file, opener)
        downloaded += 1
        total += file.size
    stamp = {
        "name": model.name,
        "repo_id": model.repo_id,
        "revision": model.revision,
        "files": [file.path for file in model.files],
    }
    (folder / STAMP).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    progress(f"{model.name} verified: {len(model.files)} files in {folder}")
    return PullResult(model.name, folder, total, downloaded, present)
