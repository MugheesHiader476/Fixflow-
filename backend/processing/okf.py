"""Read OKF concepts while leaving ordinary uploaded Markdown unchanged."""

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath

import yaml  # type: ignore[import-untyped]  # PyYAML has no bundled typing; parsed values are checked below.

MAX_FRONTMATTER_CHARS = 64 * 1024
MAX_METADATA_NODES = 10_000
MAX_METADATA_DEPTH = 32
RESERVED_NAMES = {"index.md", "log.md"}


@dataclass(frozen=True)
class OkfConcept:
    concept_id: str
    body: str
    frontmatter: dict[str, object]


def parse_concept(content: str, relative_path: str) -> OkfConcept:
    """Parse one OKF concept. Bundle validation can call this for every concept file."""
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in relative_path
        or path.suffix != ".md"
        or path.name in RESERVED_NAMES
    ):
        raise ValueError("Invalid OKF concept path")
    # Only scan the bounded frontmatter prefix, even for a large uploaded document.
    lines = content[: MAX_FRONTMATTER_CHARS + 16].splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("OKF concept requires YAML frontmatter")
    frontmatter_lines: list[str] = []
    length = 0
    body_start = None
    offset = len(lines[0])
    for line in lines[1:]:
        offset += len(line)
        if line.rstrip("\r\n") == "---":
            body_start = offset
            break
        length += len(line)
        if length > MAX_FRONTMATTER_CHARS:
            raise ValueError("OKF frontmatter is too large")
        frontmatter_lines.append(line)
    if body_start is None:
        raise ValueError("OKF concept has no closing frontmatter delimiter")
    try:
        parsed = yaml.safe_load("".join(frontmatter_lines))
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError("Invalid OKF YAML frontmatter") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError("OKF frontmatter must be a mapping with string keys")
    kind = parsed.get("type")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("OKF concept requires a nonempty type")
    normalized = _json_value(parsed, set(), [MAX_METADATA_NODES], 0)
    if not isinstance(normalized, dict):
        raise ValueError("OKF frontmatter must be a mapping")
    return OkfConcept(path.as_posix()[:-3], content[body_start:], normalized)


def maybe_parse_concept(content: str, relative_path: str) -> OkfConcept | None:
    """Recognize standalone OKF uploads without rejecting existing Markdown."""
    try:
        return parse_concept(content, relative_path)
    except ValueError:
        return None


def _json_value(value: object, active: set[int], remaining: list[int], depth: int) -> object:
    remaining[0] -= 1
    if remaining[0] < 0 or depth > MAX_METADATA_DEPTH:
        raise ValueError("OKF frontmatter is too complex")
    if isinstance(value, str) and "\x00" in value:
        raise ValueError("OKF frontmatter contains null bytes")
    if isinstance(value, dict) and any("\x00" in str(key) for key in value):
        raise ValueError("OKF frontmatter keys contain null bytes")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("OKF frontmatter contains a non-finite number")
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in active:
            raise ValueError("OKF frontmatter contains a cycle")
        active.add(identity)
        try:
            if isinstance(value, dict):
                return {
                    str(key): _json_value(item, active, remaining, depth + 1)
                    for key, item in value.items()
                }
            return [_json_value(item, active, remaining, depth + 1) for item in value]
        finally:
            active.remove(identity)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def concept_metadata(concept: OkfConcept) -> dict[str, object]:
    """Keep the full OKF envelope in the existing JSONB metadata projection."""
    frontmatter = concept.frontmatter
    metadata: dict[str, object] = {"okf": {"concept_id": concept.concept_id, "frontmatter": frontmatter}}
    title = concept.frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        metadata["title"] = title
    return metadata
