"""Bounded deterministic proposals with exact canonical spans, never automatic obligations."""

import re

from backend.schemas.pipeline import PipelineResult, digest
from backend.schemas.verification import Criterion

OBLIGATION = re.compile(r"\b(?:must|shall|should|required|requirement|acceptance|will)\b", re.IGNORECASE)
VAGUE = re.compile(r"\b(?:fast|easy|seamless|appropriate|user.friendly|robust|etc|as needed|adequate)\b", re.IGNORECASE)


def proposals(parsed: PipelineResult) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    seen: set[str] = set()
    for block in parsed.canonical.blocks:
        if block.type in {"heading", "title", "code", "metadata"}:
            continue
        for match in re.finditer(r"[^\n]+", block.content):
            text = match.group().strip()
            if not OBLIGATION.search(text):
                continue
            if len(text) > 8000:
                raise ValueError("A requirement exceeds review limits; split the source explicitly")
            if len(values) >= 100:
                raise ValueError("More than 100 requirement proposals; review a smaller source section")
            identity = digest(text.casefold())
            duplicate = identity in seen
            seen.add(identity)
            identifier = f"REQ-{len(values) + 1:03d}"
            start = match.start() + match.group().index(text)
            ambiguities = ["Acceptance policy needs human validation"]
            if duplicate:
                ambiguities.append("Duplicate contractual wording; confirm whether this is a separate obligation")
            if VAGUE.search(text):
                ambiguities.append("Ambiguous wording needs a measurable acceptance condition")
            criterion = Criterion(id=identifier + "-C1", description=text, category="technical", kind="manual")
            values.append(
                {
                    "stable_id": identifier,
                    "original_text": text,
                    "interpretation": text,
                    "acceptance_criteria": [criterion.model_dump(mode="json")],
                    "ambiguities": ambiguities,
                    "engineering_links": [],
                    "source_location": {
                        "block_id": block.block_id,
                        "start": start,
                        "end": start + len(text),
                        "section_path": block.section_path,
                        "provenance": block.provenance.model_dump(mode="json"),
                    },
                }
            )
    if not values:
        raise ValueError("No explicit requirement language found; supply a reviewed SRS or contract")
    return values
