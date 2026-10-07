"""One checked-in model identity for document/query compatibility."""

import json
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

from backend.config import ROOT, get_settings
from backend.services.embedding_profiles import FormatVersion, ModelProfile, embedding_identity, profile_for


class RetrievalPin(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    model: str
    model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    dimension: int = Field(gt=0)
    formatting_version: FormatVersion
    truncate: bool

    @property
    def profile(self) -> ModelProfile:
        profile = profile_for(self.model)
        if profile.dimension != self.dimension or profile.version != self.formatting_version or self.truncate:
            raise ValueError("Invalid retrieval model pin")
        return profile

    def identity(self, text: str) -> dict[str, object]:
        return embedding_identity(self.model, self.model_digest, self.dimension, self.formatting_version, text)

    def check_runtime(self) -> None:
        settings = get_settings()
        if not settings.ollama_url or (
            settings.embedding_model,
            settings.embedding_model_digest,
            settings.embedding_dim,
            settings.embedding_profile,
        ) != (self.model, self.model_digest, self.dimension, self.formatting_version):
            raise ValueError("Retrieval requires the pinned local embedding configuration")


@lru_cache
def retrieval_pin() -> RetrievalPin:
    pin = RetrievalPin.model_validate(json.loads((ROOT / "configs/embedding-winner.json").read_text()))
    _ = pin.profile
    return pin
