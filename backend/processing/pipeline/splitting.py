"""Bounded, lossless boundary candidates. No fragment is claimed to be standalone syntax."""

import ast
import json
import re
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from xml.parsers import expat

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class Anchor:
    offset: int
    path: str = ""
    row: int | None = None
    column: int | None = None


def pack_spans(
    text: str,
    fits: Callable[[str], bool],
    boundaries: list[int],
    window: int | None = None,
) -> list[tuple[int, int]]:
    """Prefer supplied boundaries, then whitespace, then a Unicode code-point continuation.

    Indexing is in Python characters; encode only for measurement, never slice UTF-8 bytes.
    All whitespace belongs to a part. There is no overlap or missing separator.
    """
    points = sorted({0, len(text), *boundaries})
    output: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        low, high = start + 1, min(len(text), start + window) if window else len(text)
        if not fits(text[start:low]):
            raise ValueError("Chunk policy leaves no budget for one Unicode character")
        while low < high:
            middle = (low + high + 1) // 2
            if fits(text[start:middle]):
                low = middle
            else:
                high = middle - 1
        index = bisect_right(points, low) - 1
        preferred = points[index]
        # Avoid returning a tiny header when a useful lower-level boundary is available.
        if preferred > start + (low - start) // 3:
            end = preferred
        else:
            spaces = list(re.finditer(r"\s+", text[start:low]))
            lexical = start + spaces[-1].end() if spaces else start
            end = lexical if lexical > start + (low - start) // 2 else low
        output.append((start, end))
        start = end
    return output


def prose_boundaries(text: str, clauses: bool = True) -> list[int]:
    pattern = r"[.!?](?=\s)|\n\s*\n"
    if clauses:
        pattern += r"|[;:,—](?=\s)|[()]"
    return [m.end() for m in re.finditer(pattern, text)]


def lexical_code(text: str) -> list[int]:
    """String/comment-aware punctuation boundaries, including minified statements.

    Conservative lexer, not a language validator. Regex literals/templates and exotic
    language syntax may require continuation, whose original source is retained.
    """
    points: list[int] = []
    index = 0
    quote = ""
    comment = ""
    while index < len(text):
        char = text[index]
        pair = text[index : index + 2]
        if comment == "line":
            if char == "\n":
                comment = ""
                points.append(index + 1)
        elif comment == "block":
            if pair == "*/":
                comment = ""
                index += 1
        elif quote:
            if char == "\\":
                index += 1
            elif text.startswith(quote, index):
                index += len(quote) - 1
                quote = ""
        elif pair in {"//", "/*"}:
            comment = "line" if pair == "//" else "block"
            index += 1
        elif char in "\"'`":
            quote = char * 3 if text.startswith(char * 3, index) else char
            index += len(quote) - 1
        elif char in ";,{}\n":
            points.append(index + 1)
        index += 1
    return points


def python_boundaries(text: str) -> list[int]:
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    points = lexical_code(text)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.stmt) and node.end_lineno:
            points.append(offsets[node.end_lineno])
    return points


def code_symbols(text: str) -> list[dict[str, object]]:
    """Conservative declaration evidence for brace languages; not semantic inference."""
    braces: list[int] = []
    ends: dict[int, int] = {}
    for endpoint in lexical_code(text):
        position = endpoint - 1
        if text[position] == "{":
            braces.append(position)
        elif text[position] == "}" and braces:
            ends[braces.pop()] = position
    pattern = re.compile(
        r"(?m)(?:^|(?<=[;{}]))[ \t]*(?:(?:export|public|private|protected|static|async|pub)\s+"
        r"|(?:public|private|protected):\s*)*"
        r"(?:(?:class|namespace|struct)\s+(?P<class>[A-Za-z_]\w*)[^\n{]*"
        r"|(?:function|fn|func)\s+(?P<function>[A-Za-z_]\w*)[^\n{]*"
        r"|(?:[A-Za-z_]\w*(?:[<>:*&\[\]]|\s)+)?(?P<method>[A-Za-z_]\w*)\([^\n{};]*\)[^\n{;]*)\{"
    )
    declarations: list[tuple[int, int, str]] = []
    for match in pattern.finditer(text):
        opening = match.end() - 1
        name = match.group("class") or match.group("function") or match.group("method")
        if opening in ends and name not in {"if", "while", "for", "switch", "catch"}:
            declarations.append((match.start(), ends[opening] + 1, name))
    return [
        {
            "path": [name for left, right, name in declarations if left <= start and right >= end],
            "kind": "lexical_declaration",
            "line_start": text[:start].count("\n") + 1,
            "line_end": text[:end].count("\n") + 1,
        }
        for start, end, _ in declarations
    ]


def json_anchors(text: str) -> list[Anchor]:
    """Walk the actual canonical serialization iteratively; retain RFC6901 paths.

    JSON was validated upstream. A string value is one token, so only the final
    continuation fallback can cut inside it; its path still identifies the value.
    """
    tokens = list(re.finditer(r'"(?:[^"\\]|\\.)*"|[{}\[\],:]|[^\s{}\[\],:]+', text))
    stack: list[tuple[str, str, int]] = []
    pending_key: str | None = None
    anchors = [Anchor(0, "")]
    for index, match in enumerate(tokens):
        token = match.group()
        parent = stack[-1] if stack else ("", "", 0)
        path = parent[0]
        if token.startswith('"') and index + 1 < len(tokens) and tokens[index + 1].group() == ":":
            pending_key = str(json.loads(token)).replace("~", "~0").replace("/", "~1")
            anchors.append(Anchor(match.start(), path + "/" + pending_key))
            continue
        if token in {":", ","}:
            continue
        if token in {"}", "]"}:
            if stack:
                closed, _, _ = stack.pop()
                anchors.append(Anchor(match.end(), closed))
            continue
        if stack:
            if parent[1] == "{":
                path += "/" + (pending_key or "")
                pending_key = None
            else:
                path += "/" + str(parent[2])
                stack[-1] = (parent[0], parent[1], parent[2] + 1)
        anchors.append(Anchor(match.start(), path))
        if token in {"{", "["}:
            stack.append((path, token, 0))
        else:
            anchors.append(Anchor(match.end(), path))
    return anchors


def yaml_anchors(text: str) -> list[Anchor]:
    """Validate a raw YAML tree without coercing huge numeric scalars into integers."""
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    if root is None:
        raise ValueError("Empty YAML")
    tasks = [(root, "", False)]
    active: set[int] = set()
    seen: set[int] = set()
    anchors = [Anchor(0, "")]
    while tasks:
        node, path, leaving = tasks.pop()
        if leaving:
            active.remove(id(node))
            continue
        if id(node) in active:
            raise ValueError("YAML contains a cycle")
        if id(node) in seen:
            continue  # Alias reference remains exact raw syntax; don't re-expand it.
        seen.add(id(node))
        if len(seen) > 100000 or not str(node.tag).startswith("tag:yaml.org,2002:"):
            raise ValueError("Unsupported or excessive YAML structure")
        active.add(id(node))
        tasks.append((node, path, True))
        anchors.append(Anchor(node.start_mark.index, path))
        if isinstance(node, yaml.MappingNode):
            if node.tag != "tag:yaml.org,2002:map":
                raise ValueError("Unsupported YAML mapping tag")
            keys: set[str] = set()
            for key, child in reversed(node.value):
                if key.tag != "tag:yaml.org,2002:str" or key.value in keys or "\x00" in key.value:
                    raise ValueError("YAML requires unique string keys")
                keys.add(key.value)
                escaped = key.value.replace("~", "~0").replace("/", "~1")
                anchors.append(Anchor(key.start_mark.index, path + "/" + escaped))
                tasks.append((child, path + "/" + escaped, False))
        elif isinstance(node, yaml.SequenceNode):
            if node.tag != "tag:yaml.org,2002:seq":
                raise ValueError("Unsupported YAML sequence tag")
            tasks.extend((child, path + "/" + str(i), False) for i, child in reversed(list(enumerate(node.value))))
        elif isinstance(node, yaml.ScalarNode):
            if node.tag not in {
                "tag:yaml.org,2002:" + kind for kind in ("str", "int", "float", "bool", "null", "timestamp", "binary")
            }:
                raise ValueError("Unsupported YAML scalar tag")
            if "\x00" in node.value:
                raise ValueError("YAML contains null bytes")
            if node.tag == "tag:yaml.org,2002:float" and node.value.lower().lstrip("+-") in {".inf", ".nan"}:
                raise ValueError("Non-finite YAML constant")
            anchors.append(Anchor(node.end_mark.index, path))
        else:
            raise ValueError("Unsupported YAML node")
    return sorted(anchors, key=lambda anchor: anchor.offset)


def xml_anchors(text: str) -> list[Anchor]:
    """SAX byte positions mapped to exact characters; preserve mixed text/tails/attributes."""
    byte_positions = [0]
    for char in text:
        byte_positions.append(byte_positions[-1] + len(char.encode()))
    parser = expat.ParserCreate()
    stack: list[str] = []
    siblings: list[dict[str, int]] = [{}]
    anchors = [Anchor(0, "")]

    def anchor(path: str) -> None:
        anchors.append(Anchor(bisect_right(byte_positions, parser.CurrentByteIndex) - 1, path))

    def start(name: str, attributes: dict[str, str]) -> None:
        number = siblings[-1].get(name, 0) + 1
        siblings[-1][name] = number
        stack.append(f"{name}[{number}]")
        siblings.append({})
        anchor("/" + "/".join(stack))

    def end(name: str) -> None:
        anchor("/" + "/".join(stack))
        stack.pop()
        siblings.pop()

    def data(value: str) -> None:
        anchor("/" + "/".join(stack) + "/text()")

    def forbidden(*args: object) -> None:
        raise ValueError("XML entities and DTD are not allowed")

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.CharacterDataHandler = data
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = lambda *args: 0
    parser.Parse(text, True)
    return anchors


def table_anchors(text: str, headers: list[str], rows: list[list[str]]) -> list[Anchor]:
    """Offsets in the canonical renderer; row zero is the complete column schema."""
    anchors: list[Anchor] = []
    offset = 0
    for row, cells in [(0, headers), (-1, ["---"] * len(headers)), *enumerate(rows, 1)]:
        anchors.append(Anchor(offset, "table/schema" if row <= 0 else f"table/row/{row}", max(0, row)))
        offset += 2  # '| '
        for column, cell in enumerate(cells):
            anchors.append(Anchor(offset, f"table/row/{max(0, row)}/column/{column}", max(0, row), column))
            offset += len(cell.replace("|", "\\|").replace("\n", "<br>")) + 3
        offset -= 1  # final ' |' instead of ' | '
        offset += 1  # newline (final endpoint is ignored)
    if offset - 1 != len(text):
        raise ValueError("Table canonical offsets do not match the renderer")
    return anchors
