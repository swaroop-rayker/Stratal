"""Typed errors with stable codes (ADR-0011).

Raise these from anywhere. The API turns them into RFC 9457 problem details;
failed jobs store the same ``code`` with a reason and a suggested action.
Codes are part of the public contract: add new ones, never rename.
"""

from typing import Any, ClassVar, TypedDict


class FieldProblem(TypedDict):
    """One field-level problem inside a validation failure."""

    loc: list[str | int]
    msg: str
    code: str


class StratalError(Exception):
    """Base class for every expected failure."""

    code: ClassVar[str] = "internal_error"
    title: ClassVar[str] = "Internal error"
    http_status: ClassVar[int] = 500

    def __init__(
        self,
        detail: str,
        *,
        suggested_action: str | None = None,
        errors: list[FieldProblem] | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.suggested_action = suggested_action
        self.errors: list[FieldProblem] = errors if errors is not None else []
        self.extensions: dict[str, Any] = extensions or {}
        """Extra members of the problem document (RFC 9457 §3.2), e.g. a plan to confirm."""


class ValidationFailed(StratalError):
    code = "validation_error"
    title = "Validation failed"
    http_status = 422


class NotFound(StratalError):
    code = "not_found"
    title = "Not found"
    http_status = 404


class Conflict(StratalError):
    code = "conflict"
    title = "Conflict"
    http_status = 409


class ConfirmationRequired(StratalError):
    """A change would delete data: the problem carries what would go, and the request is
    sent again with its ``token`` to confirm (REG-2.10, ADR-0015 rule 7)."""

    code = "confirmation_required"
    title = "Confirmation required"
    http_status = 409


class InvalidStateTransition(StratalError):
    code = "invalid_state_transition"
    title = "Invalid state transition"
    http_status = 409


class SourceNotFound(StratalError):
    code = "source_not_found"
    title = "Source not found"
    http_status = 422


class ModelNotAllowed(StratalError):
    """A model name that is not on the allow-list (NFR-8)."""

    code = "model_not_allowed"
    title = "Model not allowed"
    http_status = 422


class ModelIntegrityFailed(StratalError):
    """A downloaded model file did not match its pinned size or hash (NFR-8)."""

    code = "model_integrity_failed"
    title = "Model file failed its integrity check"
    http_status = 502


class ModelNotPulled(StratalError):
    """An allowed model that has not been downloaded; run time never downloads (NFR-7)."""

    code = "model_not_pulled"
    title = "Model not pulled"
    http_status = 409


class GpuOom(StratalError):
    """PyTorch ran out of VRAM and a smaller batch did not help (RUN-1.5, BUG2)."""

    code = "gpu_oom"
    title = "GPU out of memory"
    http_status = 500


class VramInsufficient(StratalError):
    """A model load was refused because free VRAM is below its estimate (ADR-0006 rule 4)."""

    code = "vram_insufficient"
    title = "Not enough free VRAM"
    http_status = 409


class WeightsDeleted(StratalError):
    """A version's weights were deleted by retention; its metadata remains (REG-2.7)."""

    code = "weights_deleted"
    title = "Weights deleted"
    http_status = 409


class WeightsUnreadable(StratalError):
    """A weights or checkpoint file is not valid safetensors. It was refused before any of
    it was used, and nothing in it ran (NFR-8)."""

    code = "weights_unreadable"
    title = "Weights unreadable"
    http_status = 409


class GpuBusy(StratalError):
    """Another process holds the GPU lease (BUG6, ADR-0006 rule 2)."""

    code = "gpu_busy"
    title = "GPU busy"
    http_status = 409


class CudaUnavailable(StratalError):
    """The GPU cannot be reached, e.g. after a sleep (ADR-0008)."""

    code = "cuda_unavailable"
    title = "CUDA unavailable"
    http_status = 503


class DependencyUnavailable(StratalError):
    code = "dependency_unavailable"
    title = "Dependency unavailable"
    http_status = 503


class WrongInput(StratalError):
    """A prediction request whose input does not suit the model (ADR-0019 rule 21): an
    image for a table model, a record with an unknown or missing column, and so on."""

    code = "wrong_input"
    title = "Wrong input for this model"
    http_status = 422


class NotServable(StratalError):
    """A registered model that cannot score a new input, e.g. DBSCAN and HDBSCAN, which
    assign clusters only to the rows they were fitted on (ADR-0019 rule 21)."""

    code = "not_servable"
    title = "This model cannot serve predictions"
    http_status = 409


class LlmUnavailable(StratalError):
    """The LLM service is down, timed out, is still preparing its model, or did not give
    VRAM back after an unload (ADR-0021 rules 5 and 10)."""

    code = "llm_unavailable"
    title = "LLM unavailable"
    http_status = 503


class LlmBusy(StratalError):
    """Answers run one at a time and the queue of waiting requests is full (ADR-0021 rule 8)."""

    code = "llm_busy"
    title = "LLM busy"
    http_status = 503


class NoImageEmbeddings(StratalError):
    """Image search on a version with no CLIP image vectors: the label stage computes them,
    a query never does (LLM-2.4, ADR-0022 rule 12)."""

    code = "no_image_embeddings"
    title = "No image vectors to search"
    http_status = 409
