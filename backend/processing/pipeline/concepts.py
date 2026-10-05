"""Grounded structural concepts and OKF v0.2 generation, with conservative evidence deduplication."""

import posixpath
import re
from collections import defaultdict
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]
from markdown_it import MarkdownIt

from backend.processing.okf import parse_concept
from backend.processing.pipeline.parsers import render_table
from backend.schemas.pipeline import Block, CanonicalDocument, Concept, PipelineResult, digest

SPEC_URL = "https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md"


def render_block(block: Block) -> str:
    if block.type == "heading":
        return "#" * min(6, int(str(block.structured_content.get("level", 1)))) + " " + block.content
    if block.type == "code":
        language = str(block.structured_content.get("language", "text"))
        longest = max((len(m.group()) for m in re.finditer(r"`+", block.content)), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}{language}\n{block.content}\n{fence}"
    if block.type == "table":
        return render_table(block.structured_content["headers"], block.structured_content["rows"])  # type: ignore[arg-type]
    return block.content


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60] or "concept"


def dependency_hash(blocks: list[Block]) -> str:
    return digest([b.model_dump(mode="json") for b in blocks])


def extract_concepts(
    document: CanonicalDocument, version: str, previous: PipelineResult | None = None
) -> list[Concept]:
    # Highest-level sections represent topics; paragraphs are evidence within them.
    groups: dict[tuple[str, ...], list[Block]] = defaultdict(list)
    for block in document.blocks:
        groups[tuple(block.section_path[:1])].append(block)
    uploaded = document.metadata.get("uploaded_okf")
    if isinstance(uploaded, dict):
        groups = {(): document.blocks}
    if () in groups and len(groups) > 1 and all(b.type == "code" for b in groups[()]):
        # A module prologue is context for the first symbol, rather than a tiny standalone import concept.
        first = next(path for path in groups if path)
        groups[first] = [*groups.pop(()), *groups[first]]
    if not isinstance(uploaded, dict):
        for section in document.sections:
            if len(section.path) > 1:
                descendants = [b for b in document.blocks if b.section_path[: len(section.path)] == section.path]
                if any(b.type not in {"heading", "title", "figure", "metadata"} for b in descendants):
                    groups[tuple(section.path)] = descendants
    concepts: list[Concept] = []
    old = {c.concept_id: c for c in previous.concepts} if previous else {}
    seen: dict[tuple[str, str], Concept] = {}
    for path, blocks in groups.items():
        if not any(b.type not in {"heading", "title", "figure", "metadata"} for b in blocks):
            continue
        title = path[-1] if path else str(document.metadata.get("title", document.source.filename))
        if isinstance(uploaded, dict) and uploaded.get("title"):
            title = str(uploaded["title"])
        identifier = f"concepts/{slug(title)}-{digest([document.source.source_id, path])[:12]}"
        body = "\n\n".join(render_block(b) for b in blocks)
        evidence_hash = digest([path, [b.content_hash for b in blocks]])
        key = (slug(title), evidence_hash)
        # A matching name alone never merges two topics. Both structural context and evidence must agree.
        if key in seen and seen[key].section_path == list(path):
            seen[key].source_block_ids.extend(b.block_id for b in blocks)
            continue
        dependency = dependency_hash(blocks)
        if identifier in old and old[identifier].dependency_hash == dependency:
            concepts.append(old[identifier].model_copy(deep=True))
            seen[key] = concepts[-1]
            continue
        uploaded = document.metadata.get("uploaded_okf")
        kind = str(uploaded.get("type", "Reference")) if isinstance(uploaded, dict) else "Reference"
        metadata: dict[str, object] = {
            "type": kind,
            "title": title,
            "sources": [
                {
                    "id": document.source.source_id,
                    "resource": f"source:{document.source.source_id}",
                    "title": document.source.filename,
                }
            ],
            "generated": {"by": f"fixflow/{version}", "at": document.source.ingested_at.isoformat()},
            "status": "draft",
            "fixflow": {
                "source_block_ids": [b.block_id for b in blocks[:128]],
                "canonical_document_id": document.document_id,
                "source_block_count": len(blocks),
                "concept_hash": evidence_hash,
            },
        }
        if isinstance(uploaded, dict):
            metadata["original_frontmatter"] = uploaded
            if kind == "Attested Computation":
                for field_name in ("runtime", "parameters", "computation", "executor", "attester"):
                    if field_name in uploaded:
                        metadata[field_name] = uploaded[field_name]
        markdown = "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + "---\n" + body + "\n"
        aliases = re.findall(r"\b([A-Z][A-Z0-9]{1,8})\s*\(([^()\n]{3,100})\)", body)
        aliases.extend(
            (short, full.strip())
            for full, short in re.findall(r"([A-Za-z][A-Za-z ]{2,80})\s*\(([A-Z][A-Z0-9]{1,8})\)", body)
        )
        concept = Concept(
            concept_id=identifier,
            title=title,
            type=kind,
            section_path=list(path),
            source_block_ids=[b.block_id for b in blocks],
            aliases=sorted({v for pair in aliases for v in pair}),
            content_hash=evidence_hash,
            dependency_hash=dependency,
            markdown=markdown,
        )
        seen[key] = concept
        concepts.append(concept)
    if not concepts:
        raise ValueError("No grounded concepts with substantive evidence")
    by_path = {tuple(c.section_path): c for c in concepts}
    for concept in concepts:
        parent = next(
            (
                by_path[tuple(concept.section_path[:n])]
                for n in range(len(concept.section_path) - 1, -1, -1)
                if tuple(concept.section_path[:n]) in by_path
                and set(concept.source_block_ids) <= set(by_path[tuple(concept.section_path[:n])].source_block_ids)
            ),
            None,
        )
        concept.parent_concept_id = parent.concept_id if parent else None
        concept.child_concept_ids = [
            c.concept_id for c in concepts if c is not concept and c.parent_concept_id == concept.concept_id
        ]
    # Parent links are now complete; direct evidence excludes each immediate child's subtree.
    for concept in concepts:
        concept.child_concept_ids = [c.concept_id for c in concepts if c.parent_concept_id == concept.concept_id]
        child_blocks = {i for c in concepts if c.parent_concept_id == concept.concept_id for i in c.source_block_ids}
        concept.direct_block_ids = [i for i in concept.source_block_ids if i not in child_blocks]
    order = {b.block_id: index for index, b in enumerate(document.blocks)}
    concepts.sort(key=lambda c: min(order[i] for i in c.source_block_ids))
    return concepts


def resolve_concepts(concepts: list[Concept], document: CanonicalDocument) -> tuple[list[Concept], int]:
    """Merge aliases only with identical substantive evidence and the same structural parent."""
    by_block = {b.block_id: b for b in document.blocks}
    resolved: list[Concept] = []
    merged = 0
    for concept in concepts:
        names = {slug(name) for name in [concept.title, *concept.aliases]}
        evidence = {
            by_block[i].content_hash for i in concept.source_block_ids if by_block[i].type not in {"heading", "title"}
        }
        target = next(
            (
                candidate
                for candidate in resolved
                if not candidate.child_concept_ids
                and not concept.child_concept_ids
                and candidate.section_path[:-1] == concept.section_path[:-1]
                and names & {slug(n) for n in [candidate.title, *candidate.aliases]}
                and evidence
                == {
                    by_block[i].content_hash
                    for i in candidate.source_block_ids
                    if by_block[i].type not in {"heading", "title"}
                }
            ),
            None,
        )
        if target is None:
            resolved.append(concept)
            continue
        merged += 1
        target.resolved_concept_ids = list(
            dict.fromkeys([*target.resolved_concept_ids, concept.concept_id, *concept.resolved_concept_ids])
        )
        target.source_block_ids = list(dict.fromkeys([*target.source_block_ids, *concept.source_block_ids]))
        target.direct_block_ids = list(dict.fromkeys([*target.direct_block_ids, *concept.direct_block_ids]))
        target.aliases = sorted({target.title, concept.title, *target.aliases, *concept.aliases})
        blocks = [by_block[i] for i in target.source_block_ids]
        target.dependency_hash = dependency_hash(blocks)
        target.content_hash = digest([target.section_path, [b.content_hash for b in blocks]])
        parsed = parse_concept(target.markdown, target.concept_id + ".md")
        fields = parsed.frontmatter
        fields["aliases"] = target.aliases
        fields["fixflow"] = {
            "source_block_ids": target.source_block_ids[:128],
            "canonical_document_id": document.document_id,
            "source_block_count": len(target.source_block_ids),
            "concept_hash": target.content_hash,
        }
        unique = {b.content_hash: b for b in blocks}
        body = "\n\n".join(render_block(b) for b in unique.values())
        target.markdown = "---\n" + yaml.safe_dump(fields, sort_keys=False, allow_unicode=True) + "---\n" + body + "\n"
    for concept in resolved:
        concept.child_concept_ids = [c.concept_id for c in resolved if c.parent_concept_id == concept.concept_id]
    return resolved, merged


def validate_okf(concepts: list[Concept], document: CanonicalDocument) -> list[str]:
    """Only type is universally required by OKF. Additional checks enforce this producer's guarantees."""
    ids = {c.concept_id for c in concepts}
    if len(ids) != len(concepts):
        raise ValueError("Duplicate OKF concept identities")
    blocks = {b.block_id for b in document.blocks}
    by_id = {c.concept_id: c for c in concepts}
    direct = [i for c in concepts for i in c.direct_block_ids]
    if set(direct) != blocks or len(direct) != len(blocks):
        raise ValueError("Concept direct evidence must partition canonical elements")
    warnings: list[str] = []
    for concept in concepts:
        if len(concept.source_block_ids) != len(set(concept.source_block_ids)) or not set(
            concept.direct_block_ids
        ) <= set(concept.source_block_ids):
            raise ValueError("Invalid concept direct evidence")
        if concept.parent_concept_id:
            parent = by_id.get(concept.parent_concept_id)
            if (
                parent is None
                or concept.concept_id not in parent.child_concept_ids
                or not set(concept.source_block_ids) <= set(parent.source_block_ids)
                or len(concept.section_path) <= len(parent.section_path)
                or concept.section_path[: len(parent.section_path)] != parent.section_path
            ):
                raise ValueError("Invalid concept hierarchy")
        if any(i not in by_id or by_id[i].parent_concept_id != concept.concept_id for i in concept.child_concept_ids):
            raise ValueError("Invalid concept children")
        parsed = parse_concept(concept.markdown, concept.concept_id + ".md")
        fields = parsed.frontmatter
        if not concept.source_block_ids or not set(concept.source_block_ids) <= blocks:
            raise ValueError("Concept evidence is missing")
        evidence = [b for b in document.blocks if b.block_id in concept.source_block_ids]
        if (
            [b.block_id for b in evidence] != concept.source_block_ids
            or concept.content_hash != digest([concept.section_path, [b.content_hash for b in evidence]])
            or concept.dependency_hash != dependency_hash(evidence)
        ):
            raise ValueError("Invalid concept evidence order or hash")
        normalized_body = re.sub(r"\s+", "", parsed.body)
        for block in document.blocks:
            if block.block_id in concept.source_block_ids and re.sub(r"\s+", "", block.content) not in normalized_body:
                raise ValueError("Concept body lost canonical evidence")
        if fields.get("status", "stable") not in {"draft", "stable", "deprecated"}:
            raise ValueError("Invalid OKF lifecycle")
        if fields.get("verified"):
            raise ValueError("Generated concepts cannot claim verification")
        generated = fields.get("generated")
        if not isinstance(generated, dict) or not isinstance(generated.get("by"), str):
            raise ValueError("Invalid OKF generation metadata")
        if str(generated["by"]).startswith("human:"):
            raise ValueError("Machine-generated content cannot claim a human author")
        try:
            timestamp = datetime.fromisoformat(str(generated["at"]))
            if timestamp.utcoffset() is None:
                raise ValueError("Timestamp must have UTC offset")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Invalid OKF generation timestamp") from error
        sources = fields.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("Generated concept lacks source references")
        for source in sources:
            if not isinstance(source, dict) or not source.get("resource"):
                raise ValueError("OKF source requires resource")
            if (
                source.get("id") != document.source.source_id
                or source["resource"] != f"source:{document.source.source_id}"
            ):
                raise ValueError("Unknown generated source reference")
        if fields["type"] == "Attested Computation" and not fields.get("runtime"):
            raise ValueError("Attested Computation requires runtime")
        tokens = MarkdownIt("commonmark").parse(parsed.body)
        if not tokens:
            raise ValueError("Empty OKF body")
        for token in tokens:
            for child in token.children or []:
                target = child.attrGet("href") if child.type == "link_open" else None
                if (
                    isinstance(target, str)
                    and target
                    and not urlsplit(target).scheme
                    and target.split("#")[0].endswith(".md")
                ):
                    path = posixpath.normpath(
                        target.lstrip("/")
                        if target.startswith("/")
                        else str(PurePosixPath(concept.concept_id).parent / target)
                    )
                    if path[:-3] not in ids:
                        # OKF §6.1 requires consumers to tolerate missing concept targets.
                        warnings.append("Unresolved internal source link")
    return list(dict.fromkeys(warnings))


def bundle(concepts: list[Concept]) -> dict[str, str]:
    files = {c.concept_id + ".md": c.markdown for c in concepts}
    links = "\n".join(f"- [{c.title}]({c.concept_id.rsplit('/', 1)[-1]}.md)" for c in concepts)
    files["concepts/index.md"] = "# Concepts\n\n" + links + "\n"
    files["index.md"] = "---\nokf_version: '0.2'\n---\n# Knowledge\n\n- [Concepts](concepts/index.md)\n"
    return files
