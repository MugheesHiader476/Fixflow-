import io
import json
import textwrap
from collections.abc import Iterable
from urllib.parse import urlsplit

from pypdf import PdfWriter
from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def _record(value: object) -> dict[str, object]:
    return {key: item for key, item in value.items() if isinstance(key, str)} if isinstance(value, dict) else {}


def _records(value: object) -> list[dict[str, object]]:
    return [_record(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _fields(value: object, names: Iterable[str]) -> dict[str, object]:
    record = _record(value)
    return {
        name: record[name]
        for name in names
        if name in record and isinstance(record[name], (str, int, float, bool, type(None)))
    }


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def customer_package(payload: dict[str, object]) -> dict[str, object]:
    evidence = []
    for fact in _records(payload.get("evidence")):
        if fact.get("customer_safe") is not True:
            continue
        safe = _fields(
            fact,
            (
                "id",
                "kind",
                "provider",
                "repository_id",
                "branch",
                "sha",
                "workflow_id",
                "run_id",
                "suite",
                "environment",
                "status",
                "observed_at",
                "event_at",
                "source_hash",
                "external_id",
                "title",
                "complete",
                "verified_native",
            ),
        )
        safe["cases"] = _strings(fact.get("cases"))
        safe["requirement_ids"] = _strings(fact.get("requirement_ids"))
        raw_url = fact.get("url")
        if isinstance(raw_url, str):
            try:
                url = urlsplit(raw_url)
                if url.scheme == "https" and url.hostname and not (
                    url.username or url.password or url.query or url.fragment
                ):
                    safe["url"] = raw_url
            except ValueError:
                pass
        evidence.append(safe)
    safe_ids = {fact.get("id") for fact in evidence if isinstance(fact.get("id"), str)}
    verification = _fields(
        payload.get("verification"),
        ("policy_version", "technical_status", "delivery_status", "verified_at", "input_hash"),
    )
    checks = []
    explanations = {
        "PASSED": "The verification policy found sufficient evidence for this criterion.",
        "FAILED": "The verification policy found a failure for this criterion.",
        "UNVERIFIED": "Sufficient verified evidence is unavailable for this criterion.",
        "BLOCKED": "A required approval or prerequisite remains outstanding.",
        "NOT_APPLICABLE": "This criterion does not apply to this verification.",
    }
    for check in _records(_record(payload.get("verification")).get("checks")):
        safe_check = _fields(check, ("criterion_id", "category", "status"))
        status = safe_check.get("status")
        safe_check["explanation"] = explanations.get(str(status), explanations["UNVERIFIED"])
        safe_check["evidence_ids"] = [
            identifier for identifier in _strings(check.get("evidence_ids")) if identifier in safe_ids
        ]
        checks.append(safe_check)
    verification["checks"] = checks
    requirements = []
    for requirement in _records(payload.get("requirements")):
        safe_requirement = _fields(requirement, ("stable_id", "original_text"))
        criteria = []
        for criterion in _records(requirement.get("acceptance_criteria")):
            safe_criterion = _fields(
                criterion,
                (
                    "id",
                    "description",
                    "category",
                    "kind",
                    "mandatory",
                    "repository_id",
                    "branch",
                    "sha",
                    "workflow_id",
                    "run_id",
                    "suite",
                    "environment",
                    "max_age_hours",
                    "allow_skipped",
                ),
            )
            safe_criterion["cases"] = _strings(criterion.get("cases"))
            criteria.append(safe_criterion)
        safe_requirement["acceptance_criteria"] = criteria
        requirements.append(safe_requirement)
    result: dict[str, object] = {
        "brand": "ProofOS",
        "project": _fields(payload.get("project"), ("name", "customer_name")),
        "milestone": _fields(payload.get("milestone"), ("code", "title", "description")),
        "contract": _fields(payload.get("contract"), ("title", "revision", "source_hash", "source_version")),
        "verification": verification,
        "requirements": requirements,
        "evidence": evidence,
        "customer_acceptance": _fields(payload.get("customer_acceptance"), ("status",)),
    }
    result.update(_fields(payload, ("generated_at", "package_version")))
    return result


def _paragraphs(payload: dict[str, object]) -> list[tuple[str, bool]]:
    paragraphs: list[tuple[str, bool]] = [("Milestone acceptance package", True)]
    for title, key, names in (
        ("Project", "project", ("name", "customer_name")),
        ("Milestone", "milestone", ("code", "title", "description")),
        ("Contract baseline", "contract", ("title", "revision", "source_hash", "source_version")),
        (
            "Verification",
            "verification",
            ("technical_status", "delivery_status", "policy_version", "verified_at", "input_hash"),
        ),
        ("Customer acceptance", "customer_acceptance", ("status",)),
    ):
        record = _record(payload.get(key))
        if record:
            paragraphs.append((title, True))
            paragraphs.extend(
                (f"{name.replace('_', ' ').capitalize()}: {record[name]}", False)
                for name in names
                if record.get(name) is not None
            )
    for check in _records(_record(payload.get("verification")).get("checks")):
        paragraphs.append((f"{check.get('criterion_id', 'Criterion')}: {check.get('status', 'UNVERIFIED')}", True))
        paragraphs.append((str(check.get("explanation", "")), False))
        identifiers = _strings(check.get("evidence_ids"))
        if identifiers:
            paragraphs.append(("Evidence: " + ", ".join(identifiers), False))
    if _records(payload.get("requirements")):
        paragraphs.append(("Approved requirements", True))
    for requirement in _records(payload.get("requirements")):
        paragraphs.append((str(requirement.get("stable_id", "Requirement")), True))
        paragraphs.append((str(requirement.get("original_text", "")), False))
        paragraphs.extend(
            (f"- {criterion.get('description', '')}", False)
            for criterion in _records(requirement.get("acceptance_criteria"))
        )
    if _records(payload.get("evidence")):
        paragraphs.append(("Evidence record", True))
    for fact in _records(payload.get("evidence")):
        paragraphs.append((str(fact.get("title", fact.get("id", "Evidence"))), True))
        paragraphs.extend(
            (f"{name.replace('_', ' ').capitalize()}: {fact[name]}", False)
            for name in (
                "id",
                "kind",
                "provider",
                "status",
                "repository_id",
                "branch",
                "sha",
                "workflow_id",
                "run_id",
                "suite",
                "environment",
                "cases",
                "external_id",
                "observed_at",
                "event_at",
                "source_hash",
                "url",
                "details",
            )
            if fact.get(name) is not None
        )
    if _strings(payload.get("exceptions")):
        paragraphs.append(("Exceptions requiring review", True))
        paragraphs.extend((item, False) for item in _strings(payload.get("exceptions")))
    review = _record(payload.get("internal_review"))
    if review:
        paragraphs.append(("Internal review", True))
        paragraphs.extend(
            (f"{name}: {review[name]}", False)
            for name in ("status", "reviewer", "reviewed_at")
            if review.get(name) is not None
        )
    paragraphs.extend(
        (f"{name.replace('_', ' ').capitalize()}: {payload[name]}", False)
        for name in ("generated_at", "package_version")
        if payload.get(name) is not None
    )
    paragraphs.append(
        (
            "Verification and customer acceptance are separate records. "
            "This package is not an invoice or proof of payment.",
            False,
        )
    )
    return paragraphs


def _literal(value: str) -> str:
    encoded = value.encode("cp1252", errors="backslashreplace")
    return "<" + encoded.hex() + ">"


def _wrap(paragraph: str, heading: bool) -> list[str]:
    normalized = paragraph.replace("\r", "").replace("\t", "    ")
    normalized = normalized.encode("cp1252", errors="backslashreplace").decode("cp1252")
    metrics = CORE_FONT_METRICS["Helvetica-Bold" if heading else "Helvetica"].character_widths
    maximum = 516_000 / (11 if heading else 10)
    result = []
    for part in normalized.split("\n"):
        for candidate in textwrap.wrap(part, width=90, break_long_words=True):
            remaining = candidate
            while remaining:
                width = 0.0
                boundary = len(remaining)
                for index, character in enumerate(remaining):
                    width += metrics.get(character, metrics["default"])
                    if width > maximum:
                        boundary = index
                        break
                if boundary < len(remaining):
                    space_index = remaining.rfind(" ", 0, boundary + 1)
                    if space_index > 0:
                        boundary = space_index
                result.append(remaining[:boundary].rstrip())
                remaining = remaining[boundary:].lstrip()
    return result


def render_package(payload: dict[str, object]) -> bytes:
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False)) > 1_000_000:
        raise ValueError("Acceptance package exceeds the bounded rendering size")
    pages: list[list[tuple[str, bool]]] = [[]]
    paragraphs = _paragraphs(payload)
    for paragraph_index, (paragraph, heading) in enumerate(paragraphs):
        lines = _wrap(paragraph, heading)
        if not lines:
            continue
        reserved = len(lines) + 1
        if heading and paragraph_index + 1 < len(paragraphs):
            following, following_heading = paragraphs[paragraph_index + 1]
            reserved += min(len(_wrap(following, following_heading)), 2) + 1
        if len(pages[-1]) + min(reserved, 6) > 46:
            pages.append([])
        for line in lines:
            if len(pages[-1]) >= 46:
                pages.append([])
            pages[-1].append((line, heading))
        pages[-1].append(("", False))
    if len(pages) > 100:
        raise ValueError("Acceptance package exceeds the bounded page count")
    writer = PdfWriter()
    writer.add_metadata({"/Title": "ProofOS milestone acceptance package", "/Author": "ProofOS"})
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    bold_font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    for index, page_lines in enumerate(pages, start=1):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font, NameObject("/F2"): bold_font})}
        )
        commands = [
            "0.11 0.22 0.29 rg",
            "BT /F2 20 Tf 48 746 Td " + _literal("ProofOS") + " Tj ET",
            "0.25 0.31 0.35 rg",
        ]
        for offset, (line, heading) in enumerate(page_lines):
            font_name, size = ("F2", 11) if heading else ("F1", 10)
            commands.append(f"BT /{font_name} {size} Tf 48 {707 - offset * 13} Td {_literal(line)} Tj ET")
        commands.append(f"BT /F1 9 Tf 48 40 Td {_literal(f'Acceptance evidence | Page {index} of {len(pages)}')} Tj ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
