"""The allow-list of pretrained models (NFR-8, ARCHITECTURE.md §8 Security).

A model can be downloaded only if it is listed here, and only the files listed
for it, each pinned to a size and a hash taken from the Hugging Face repository
at a fixed commit. Weights are safetensors only: the pickle copies some
repositories also carry (``pytorch_model.bin``, ``tf_model.h5``) are never
listed, so they can never be fetched.

Hashes: large files are stored by Hugging Face in Git LFS, whose pointer records
the SHA-256 of the content. Small files are plain git blobs, identified by git's
SHA-1 of ``"blob <size>\\0" + content``. Both are checked after download.

Adding a model is a reviewed change to this file (ADR-0009 for the captioner).

LLMs (LLM-1.1, M12 decision 2) are single 4-bit GGUF files, which Ollama builds
its model from; GGUF, like safetensors, holds tensors and metadata, no code.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stratal.core.errors import ModelNotAllowed, ModelNotPulled

Role = Literal["captioner", "clip", "llm", "embedding"]
WEIGHT_SUFFIXES = (".safetensors", ".gguf")
"""The weight formats allowed: neither can carry code (NFR-8)."""
STAMP = "stratal-model.json"
"""Written last by a successful pull; its presence means every file was verified."""


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    sha256: str | None = None
    """For Git LFS files: SHA-256 of the content."""
    git_oid: str | None = None
    """For small files: git's blob SHA-1."""


@dataclass(frozen=True)
class PretrainedModel:
    name: str
    repo_id: str
    revision: str
    """Full commit hash on the Hugging Face hub."""
    role: Role
    display: str
    """How messages name it, e.g. ``Florence-2-base``."""
    licence: str
    purpose: str
    files: tuple[ModelFile, ...]

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def weights_bytes(self) -> int:
        return sum(f.size for f in self.files if f.path.endswith(WEIGHT_SUFFIXES))


def _catalog(*models: PretrainedModel) -> dict[str, PretrainedModel]:
    return {model.name: model for model in models}


CATALOG = _catalog(
    PretrainedModel(
        name="florence-2-base",
        repo_id="florence-community/Florence-2-base",
        revision="00921df66db728a9ceb750f5eca43e5c203a2051",
        role="captioner",
        display="Florence-2-base",
        licence="MIT",
        purpose="Default captioner (LABEL-1.1, ADR-0009): native transformers class",
        files=(
            ModelFile(
                "added_tokens.json", 22430, git_oid="45a441f0d1c61f68475ae6cb6aec35cd7d679671"
            ),
            ModelFile("config.json", 2387, git_oid="9d69359cb5bad2c0ca0346413bbe9dafd5521e8f"),
            ModelFile(
                "generation_config.json", 292, git_oid="bd4a1d37522d13285c2f753d75af4c07ca463167"
            ),
            ModelFile("merges.txt", 456318, git_oid="226b0752cac7789c48f0cb3ec53eda48b7be36cc"),
            ModelFile(
                "model.safetensors",
                463178864,
                sha256="62f3e696da74f8869a68ddb529a9b3e14eb25b21c592cb3dea6179bf944df6a0",
            ),
            ModelFile(
                "preprocessor_config.json", 603, git_oid="e7e0dbc98f7a0985ea42428925d863c19ce1b222"
            ),
            ModelFile(
                "processor_config.json", 2264, git_oid="2dace493360deb05053d62f362df1db802d03aa9"
            ),
            ModelFile(
                "special_tokens_map.json",
                146627,
                git_oid="d748fe272a81974cb735e2d1d6821bb294566849",
            ),
            ModelFile(
                "tokenizer.json", 3748144, git_oid="d03cc512bed8bc7e6012be4d5b20edb6a2d8ee3e"
            ),
            ModelFile(
                "tokenizer_config.json", 197922, git_oid="7aec00347e73f5f33f60a05aa382b4defc3931d6"
            ),
            ModelFile("vocab.json", 798293, git_oid="4ebe4bb3f3114daf2e4cc349f24873a1175a35d7"),
        ),
    ),
    PretrainedModel(
        name="clip-vit-b-32-laion2b",
        repo_id="laion/CLIP-ViT-B-32-laion2B-s34B-b79K",
        revision="1a25a446712ba5ee05982a381eed697ef9b435cf",
        role="clip",
        display="CLIP ViT-B/32",
        licence="MIT",
        purpose=(
            "Image-caption similarity (LABEL-1.3). OpenAI's own ViT-B/32 has safetensors only"
            " in unreviewed pull requests, so LAION's training of the same architecture is used"
        ),
        files=(
            ModelFile("config.json", 4355, git_oid="d26170f0026871b515cf2bd1107a3143f0530a65"),
            ModelFile("merges.txt", 524618, git_oid="80954e3a66452c700b68842dcebc85e08dd3306a"),
            ModelFile(
                "model.safetensors",
                605157884,
                sha256="74813fbcdc750f235c9784c367ca1394d2a5c25eb0aac92761752ac239db7cff",
            ),
            ModelFile(
                "preprocessor_config.json", 316, git_oid="5a12a1eb250987a4eee0e3e7d7338c4b22724be1"
            ),
            ModelFile(
                "special_tokens_map.json", 389, git_oid="9bfb42aa97dcd61e89f279ccaee988bccb4fabae"
            ),
            ModelFile(
                "tokenizer.json", 2224041, git_oid="564c0ebd5ce29c4ee4864004aee693deadd3128c"
            ),
            ModelFile(
                "tokenizer_config.json", 904, git_oid="e3e138b103e00cb9cfb2ab6ef11d96b47ddd9e04"
            ),
            ModelFile("vocab.json", 862328, git_oid="182766ce89b439768edadda342519f33802f5364"),
        ),
    ),
    PretrainedModel(
        name="blip-large",
        repo_id="Salesforce/blip-image-captioning-large",
        revision="353689b859fcf0523410b1806dace5fb46ecdf41",
        role="captioner",
        display="BLIP-large",
        licence="BSD-3-Clause",
        purpose=(
            "Comparison captioner for spike S-06. BLIP-base has safetensors only in unreviewed"
            " pull requests, so the large model is used"
        ),
        files=(
            ModelFile("config.json", 4597, git_oid="0aa1a9f9cd0404a26dd7e6f13c16eb4259939139"),
            ModelFile(
                "model.safetensors",
                1879014680,
                sha256="ebc867586b8318647dd7b75ef8e52f93f89b98ac7dcbd04d10219d33b9a68c6f",
            ),
            ModelFile(
                "preprocessor_config.json", 445, git_oid="9edc3be35940faf780eb39e0191d0b8a2b13c329"
            ),
            ModelFile(
                "special_tokens_map.json", 125, git_oid="a8b3208c2884c4efb86e49300fdd3dc877220cdf"
            ),
            ModelFile("tokenizer.json", 711396, git_oid="688882a79f44442ddc1f60d70334a7ff5df0fb47"),
            ModelFile(
                "tokenizer_config.json", 527, git_oid="3529d5f768b8b1ccc65b80b78fb43e16069109c1"
            ),
            ModelFile("vocab.txt", 231508, git_oid="fb140275c155a9c7c5a3b3e0e77a9e839594a938"),
        ),
    ),
    PretrainedModel(
        name="qwen3-1.7b-q4km",
        repo_id="unsloth/Qwen3-1.7B-GGUF",
        revision="d7f544eead698dbd1f15126ef60b45a1e1933222",
        role="llm",
        display="Qwen3-1.7B (Q4_K_M)",
        licence="Apache-2.0",
        purpose=(
            "The LLM (LLM-1.1), chosen by the owner from spike S-11 on 2026-11-27 (M12"
            " decision 1). Qwen publishes only an 8-bit GGUF, so Unsloth's 4-bit conversion of"
            " the same weights is used. SmolLM3-3B, the other candidate, was removed after S-11"
        ),
        files=(
            ModelFile(
                "Qwen3-1.7B-Q4_K_M.gguf",
                1107409472,
                sha256="b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
            ),
        ),
    ),
    PretrainedModel(
        name="bge-small-en-v1.5",
        repo_id="BAAI/bge-small-en-v1.5",
        revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        role="embedding",
        display="bge-small-en-v1.5",
        licence="MIT",
        purpose="Text embeddings for RAG (LLM-2.1, M12 decision 4), through sentence-transformers",
        files=(
            ModelFile(
                "1_Pooling/config.json", 190, git_oid="1da070f0993788c8851a652170ff013aa54dd519"
            ),
            ModelFile("config.json", 743, git_oid="3992bf890728a92476c700e6e657fd696eb94160"),
            ModelFile(
                "config_sentence_transformers.json",
                124,
                git_oid="dcb0c0d97d09b930d13600b1a773ddb27e441aab",
            ),
            ModelFile(
                "model.safetensors",
                133466304,
                sha256="3c9f31665447c8911517620762200d2245a2518d6e7208acc78cd9db317e21ad",
            ),
            ModelFile("modules.json", 349, git_oid="952a9b81c0bfd99800fabf352f69c7ccd46c5e43"),
            ModelFile(
                "sentence_bert_config.json", 52, git_oid="ea85692bff64b0d1917833c31ddbca8ab10f5455"
            ),
            ModelFile(
                "special_tokens_map.json", 125, git_oid="a8b3208c2884c4efb86e49300fdd3dc877220cdf"
            ),
            ModelFile("tokenizer.json", 711396, git_oid="688882a79f44442ddc1f60d70334a7ff5df0fb47"),
            ModelFile(
                "tokenizer_config.json", 366, git_oid="37fca74771bc76a8e01178ce3a6055a0995f8093"
            ),
            ModelFile("vocab.txt", 231508, git_oid="fb140275c155a9c7c5a3b3e0e77a9e839594a938"),
        ),
    ),
)


def get_model(name: str) -> PretrainedModel:
    """The allowed model called ``name``; anything else is refused (NFR-8)."""
    model = CATALOG.get(name)
    if model is None:
        raise ModelNotAllowed(
            f"{name!r} is not on the model allow-list.",
            suggested_action=f"Use one of: {', '.join(sorted(CATALOG))}, or add it to "
            "stratal/pretrained/catalog.py in a reviewed change.",
        )
    return model


def model_dir(models_dir: Path, model: PretrainedModel) -> Path:
    """Where a model's files live: one folder per name and revision."""
    return models_dir / model.name / model.revision


def local_path(models_dir: Path, name: str) -> Path:
    """The folder of a pulled, verified model. Never downloads (NFR-7)."""
    model = get_model(name)
    folder = model_dir(models_dir, model)
    if not (folder / STAMP).is_file():
        raise ModelNotPulled(
            f"Model {name!r} has not been pulled.",
            suggested_action=f"Run: python -m stratal.cli models pull {name}",
        )
    return folder
