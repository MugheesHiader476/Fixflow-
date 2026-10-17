"""Source syntax inspection retained by the native parsers; no chunk generation."""

import re
from dataclasses import dataclass

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class Anchor:
    offset: int
    path: str = ""
    row: int | None = None
    column: int | None = None


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
