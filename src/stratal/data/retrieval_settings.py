"""SCR-14's retrieval section (LLM-2.3; ADR-0022 rule 8; SCREENS.md v5).

The abstention threshold is the owner's (decision 6): there is none until it is set,
and until then RAG never abstains. It belongs to the embedding model, since relevance
is that model's cosine; ``source`` names the retrieval report it was chosen from.
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from stratal.data.corpus import EMBEDDING_MODEL
from stratal.db.models import SettingsSection
from stratal.mon.events import utc_now

RETRIEVAL = "retrieval"


class RetrievalValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding_model: str = Field(
        default=EMBEDDING_MODEL,
        json_schema_extra={"readOnly": True},
        description="The model whose relevance the threshold is measured in.",
    )
    abstention_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description='Below this top-1 relevance (cosine), RAG answers "I don\'t know" with the '
        "nearest source, without asking the model (LLM-2.3). Empty: RAG never abstains.",
    )
    threshold_source: str | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
        description="The index version whose retrieval report the threshold was chosen from.",
    )
    default_k: int = Field(
        default=5, ge=1, le=20, description="Sources given to the model per question."
    )


def read_retrieval(session: Session) -> RetrievalValues:
    row = session.get(SettingsSection, RETRIEVAL)
    if row is None:
        return RetrievalValues()
    return RetrievalValues.model_validate({**RetrievalValues().model_dump(), **row.values})


def retrieval_version(values: RetrievalValues) -> str:
    canonical = json.dumps(values.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def write_retrieval(session: Session, values: RetrievalValues) -> None:
    row: dict[str, Any] = {
        "section": RETRIEVAL,
        "values": values.model_dump(mode="json"),
        "updated_at": utc_now(),
    }
    session.execute(
        insert(SettingsSection)
        .values(**row)
        .on_conflict_do_update(index_elements=["section"], set_=row)
    )
    session.flush()
