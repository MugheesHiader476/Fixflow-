"""Replace the dependency below with an AI adapter when a provider is selected."""

from typing import Literal, Protocol

from backend.schemas.models import ChatMessage, DebugRequest, Diagnosis, DiagnosisDraft, FixStep, SourceDoc


def diagnostic_text(payload: DebugRequest) -> str:
    values = [payload.error, payload.context, payload.code, *(file.content for file in payload.files)]
    return "\n".join(value.strip() for value in values if value and value.strip())


class DiagnosisProvider(Protocol):
    generation: Literal["disabled", "model"]

    async def diagnose(self, payload: DebugRequest, evidence: list[SourceDoc]) -> DiagnosisDraft: ...

    async def reply(
        self, question: str, diagnosis: Diagnosis, history: list[ChatMessage], evidence: list[SourceDoc]
    ) -> str: ...


class DocumentationProvider:
    """Useful evidence browsing before AI is configured; never invents a fix."""

    generation: Literal["disabled", "model"] = "disabled"

    async def diagnose(self, payload: DebugRequest, evidence: list[SourceDoc]) -> DiagnosisDraft:
        return DiagnosisDraft(
            status="investigating" if evidence else "no-cause",
            detected=list(dict.fromkeys([*payload.techs, *([payload.technology] if payload.technology else [])])),
            rootCause="Related documentation found; a root cause has not been determined."
            if evidence
            else "No matching documentation found for this problem.",
            whyThisHappens="AI diagnosis is not connected yet. Your inputs are saved and searched against uploaded "
            "documentation using the configured retrieval method. "
            "Repository links are saved as context; they are not fetched.",
            recommendedFix=[
                FixStep(
                    title="Review the retrieved documentation" if evidence else "Add relevant documentation",
                    detail="Compare the excerpts below with your error and code."
                    if evidence
                    else "Upload a guide on Knowledge Sources, then run the diagnosis again.",
                )
            ],
            alternatives=[],
        )

    async def reply(
        self, question: str, diagnosis: Diagnosis, history: list[ChatMessage], evidence: list[SourceDoc]
    ) -> str:
        if evidence:
            excerpts = [f"{source.title}: {' '.join(source.excerpt.split())[:1500]}" for source in evidence]
            return "Matching documentation (AI generation is not connected):\n\n" + "\n\n".join(excerpts)
        return (
            "No matching documentation found. Try naming the error, library, or function, or upload a relevant guide."
        )


def get_diagnosis_provider() -> DiagnosisProvider:
    return DocumentationProvider()
