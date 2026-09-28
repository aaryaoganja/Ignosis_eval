"""Shared base model and constrained scalar types for all contracts."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}$"
CASE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
BCP47_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"
GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"

Id = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
CaseId = Annotated[str, StringConstraints(pattern=CASE_ID_PATTERN)]
Sha256Hex = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
LanguageTag = Annotated[str, StringConstraints(pattern=BCP47_PATTERN)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegInt = Annotated[int, Field(ge=0)]


class Contract(BaseModel):
    """Base for every serialized contract: unknown fields are rejected (no silent drift)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")
