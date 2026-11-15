"""NFR-7 and NFR-8: models come only from the allow-list, verified, and never at run time."""

import hashlib
import io
import json
import re
import tokenize
from pathlib import Path
from typing import BinaryIO

import pytest

from stratal.cli.__main__ import main
from stratal.core.errors import ModelIntegrityFailed, ModelNotAllowed, ModelNotPulled
from stratal.pretrained.catalog import (
    CATALOG,
    STAMP,
    ModelFile,
    PretrainedModel,
    get_model,
    local_path,
    model_dir,
)
from stratal.pretrained.pull import pull

SRC = Path(__file__).resolve().parents[2] / "src"
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle", ".h5", ".msgpack")


def _git_oid(content: bytes) -> str:
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def _fake_model(files: dict[str, bytes]) -> PretrainedModel:
    pinned: list[ModelFile] = []
    for path, content in files.items():
        if path.endswith(".safetensors"):
            pinned.append(ModelFile(path, len(content), sha256=hashlib.sha256(content).hexdigest()))
        else:
            pinned.append(ModelFile(path, len(content), git_oid=_git_oid(content)))
    return PretrainedModel(
        name="fake",
        repo_id="org/fake",
        revision="0" * 40,
        role="clip",
        display="Fake",
        licence="MIT",
        purpose="test",
        files=tuple(pinned),
    )


class _FakeHub:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requests: list[str] = []

    def __call__(self, url: str) -> BinaryIO:
        self.requests.append(url)
        return io.BytesIO(self.files[url.rsplit("/", 1)[-1]])


def test_NFR_8_a_model_outside_the_allow_list_is_refused() -> None:
    with pytest.raises(ModelNotAllowed, match="not on the model allow-list"):
        get_model("openai/clip-vit-base-patch32")


def test_NFR_8_the_pull_command_refuses_before_downloading_anything(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["models", "pull", "florence-2-base", "not-a-model"]) == 1

    output = json.loads(capsys.readouterr().out)
    assert output["error"]["code"] == "model_not_allowed"


def test_NFR_8_the_catalog_pins_safetensors_weights_and_every_file() -> None:
    assert {"florence-2-base", "clip-vit-b-32-laion2b", "blip-large"} <= set(CATALOG)
    for model in CATALOG.values():
        assert re.fullmatch(r"[0-9a-f]{40}", model.revision), model.name
        paths = [f.path for f in model.files]
        assert any(p.endswith(".safetensors") for p in paths), model.name
        assert not [p for p in paths if p.endswith(PICKLE_SUFFIXES)], model.name
        for file in model.files:
            assert (file.sha256 is None) != (file.git_oid is None), (model.name, file.path)
            if file.path.endswith(".safetensors"):
                assert file.sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", file.sha256)


def test_NFR_8_no_trust_remote_code_in_source() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type == tokenize.NAME and token.string == "trust_remote_code":
                    offenders.append(f"{path.relative_to(SRC)}:{token.start[0]}")
    assert offenders == []


def test_NFR_8_a_file_that_does_not_match_its_pin_is_not_kept(tmp_path: Path) -> None:
    good = {"config.json": b'{"a": 1}', "model.safetensors": b"weights" * 100}
    model = _fake_model(good)
    tampered = _FakeHub({**good, "model.safetensors": b"WEIGHTS" * 100})

    with pytest.raises(ModelIntegrityFailed, match="SHA-256"):
        pull(model, tmp_path, opener=tampered)

    folder = model_dir(tmp_path, model)
    assert not (folder / "model.safetensors").exists()
    assert not (folder / STAMP).exists()
    assert not list(folder.glob("*.partial"))


def test_NFR_8_a_file_longer_than_its_pin_is_cut_off(tmp_path: Path) -> None:
    good = {"model.safetensors": b"w" * 50}
    model = _fake_model(good)

    with pytest.raises(ModelIntegrityFailed, match="larger than"):
        pull(model, tmp_path, opener=_FakeHub({"model.safetensors": b"w" * (2 << 20)}))


def test_NFR_8_pull_verifies_and_a_second_pull_downloads_nothing(tmp_path: Path) -> None:
    files = {"config.json": b'{"a": 1}', "model.safetensors": b"weights" * 100}
    model = _fake_model(files)
    hub = _FakeHub(files)

    first = pull(model, tmp_path, opener=hub)
    second = pull(model, tmp_path, opener=hub)

    assert (first.files_downloaded, second.files_downloaded, second.files_present) == (2, 0, 2)
    assert len(hub.requests) == 2
    assert hub.requests[0] == f"https://huggingface.co/org/fake/resolve/{'0' * 40}/config.json"
    folder = model_dir(tmp_path, model)
    assert (folder / "model.safetensors").read_bytes() == files["model.safetensors"]
    assert json.loads((folder / STAMP).read_text())["revision"] == "0" * 40


def test_NFR_7_an_unpulled_model_is_reported_not_downloaded(tmp_path: Path) -> None:
    with pytest.raises(ModelNotPulled) as raised:
        local_path(tmp_path, "florence-2-base")

    assert raised.value.suggested_action is not None
    assert "models pull florence-2-base" in raised.value.suggested_action
