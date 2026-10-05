"""Native adapters preserve structure; optional OCR runs only inside the parser cascade."""

import ast
import csv
import io
import json
import re
import shutil

# Only installed OCR decoders run, with fixed argv, no shell and bounded time.
import subprocess  # nosec B404
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from itertools import pairwise
from pathlib import Path
from typing import Protocol, runtime_checkable

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag
from defusedxml import ElementTree as XML  # type: ignore[import-untyped]
from markdown_it import MarkdownIt

from backend.processing.okf import _json_value, load_yaml, maybe_parse_concept, unique_mapping
from backend.processing.pipeline.config import PipelineConfig
from backend.processing.pipeline.inspection import CODE_LANGUAGES, Inspection
from backend.schemas.pipeline import Asset, Block, BlockType, Provenance, Source, digest

VERSION = "2"


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reading_order_confidence: float | None = None


class XmlTextNode(Protocol):
    tag: str
    text: str | None

    def iter(self) -> Iterator["XmlTextNode"]: ...


@runtime_checkable
class Parser(Protocol):
    name: str
    tier: int

    def supports(self, inspection: Inspection) -> bool: ...
    def parse(self, inspection: Inspection, source: Source, config: PipelineConfig) -> Parsed: ...


class Builder:
    def __init__(self, source: Source, parser: str, config: PipelineConfig) -> None:
        self.source, self.parser, self.config = source, parser, config
        self.result = Parsed()
        self.path: list[str] = []
        self.counts: dict[str, int] = {}
        self.characters = 0

    def add(
        self,
        kind: BlockType,
        content: str,
        structured: dict[str, object] | None = None,
        *,
        location: dict[str, object] | None = None,
        confidence: float | None = None,
    ) -> None:
        if not content.strip():
            return
        self.characters += len(content)
        if self.characters > self.config.max_characters or len(self.result.blocks) >= self.config.max_blocks:
            raise ValueError("Parser output exceeds processing limits")
        structured = structured or {}
        block_hash = digest([kind, content, structured])
        key = digest([self.source.source_id, self.path, block_hash])
        occurrence = self.counts.get(key, 0)
        self.counts[key] = occurrence + 1
        provenance = Provenance.model_validate(
            {
                "source_id": self.source.source_id,
                "parser": self.parser,
                "parser_version": VERSION,
                "extraction_method": "ocr" if "ocr" in self.parser else "native",
                **(location or {}),
            }
        )
        self.result.blocks.append(
            Block(
                block_id="b-" + digest([key, occurrence])[:32],
                type=kind,
                content=content,
                structured_content=structured,
                section_path=self.path.copy(),
                provenance=provenance,
                confidence=confidence,
                content_hash=block_hash,
            )
        )

    def heading(self, title: str, level: int = 1, location: dict[str, object] | None = None) -> None:
        self.path = [*self.path[: max(0, level - 1)], title]
        self.add("heading", title, {"level": level}, location=location)

    def table(self, headers: list[str], rows: list[list[str]], location: dict[str, object] | None = None) -> None:
        if not headers or not rows or any(len(row) != len(headers) for row in rows):
            raise ValueError("Invalid table dimensions or missing data")
        headers = [h or f"Column {i + 1}" for i, h in enumerate(headers)]
        self.add("table", render_table(headers, rows), {"headers": headers, "rows": rows}, location=location)

    def asset(self, reference: str, media_type: str, location: dict[str, object] | None = None) -> None:
        identifier = "a-" + digest([self.source.source_id, reference])[:32]
        self.add("figure", f"Asset: {reference}", {"asset_id": identifier}, location=location)
        self.result.assets.append(
            Asset(
                asset_id=identifier,
                reference=reference,
                media_type=media_type,
                provenance=self.result.blocks[-1].provenance,
            )
        )


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    def row(values: list[str]) -> str:
        return "| " + " | ".join(v.replace("|", "\\|").replace("\n", "<br>") for v in values) + " |"

    return "\n".join([row(headers), row(["---"] * len(headers)), *(row(r) for r in rows)])


def markdown(text: str, builder: Builder, location: dict[str, object] | None = None) -> None:
    lines = text.splitlines(keepends=True)
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.level != 0:
            index += 1
            continue
        positions = token.map
        loc = dict(location or {})
        if positions:
            offset = int(str(loc.get("line_start", 1))) - 1
            loc.update(line_start=offset + positions[0] + 1, line_end=offset + positions[1])
        if token.type == "heading_open":
            builder.heading(tokens[index + 1].content, int(token.tag[1:]), loc)
        elif token.type in {"fence", "code_block"}:
            language = token.info.split()[0] if token.info.strip() else "text"
            builder.add("code", token.content, {"language": language}, location=loc)
        elif token.type == "table_open":
            rows: list[list[str]] = []
            end = index + 1
            while end < len(tokens) and tokens[end].type != "table_close":
                if tokens[end].type == "tr_open":
                    rows.append([])
                if tokens[end].type == "inline":
                    rows[-1].append(tokens[end].content)
                end += 1
            builder.table(rows[0], rows[1:], loc)
            index = end
        elif token.type in {"bullet_list_open", "ordered_list_open", "blockquote_open"} and positions:
            kind: BlockType = "quote" if token.type == "blockquote_open" else "list"
            builder.add(kind, "".join(lines[positions[0] : positions[1]]).rstrip("\r\n"), location=loc)
            close = token.type.replace("open", "close")
            index += 1
            while index < len(tokens) and not (tokens[index].type == close and tokens[index].level == 0):
                index += 1
        elif token.type == "paragraph_open":
            content = tokens[index + 1].content
            kind = "equation" if content.startswith("$$") and content.endswith("$$") else "paragraph"
            builder.add(kind, content, {"latex": content[2:-2]} if kind == "equation" else {}, location=loc)
        index += 1


def python_code(text: str, builder: Builder) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise ValueError("Python source has invalid syntax") from error
    lines = text.splitlines(keepends=True)
    start = 0
    for node in tree.body:
        first = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]]) - 1
        last = node.end_lineno or node.lineno
        if first > start:
            builder.add(
                "code",
                "".join(lines[start:first]),
                {"language": "python"},
                location={"line_start": start + 1, "line_end": first},
            )
        symbol = getattr(node, "name", None)
        if symbol:
            builder.heading(symbol, 1, {"line_start": first + 1, "line_end": last})
        builder.add(
            "code",
            "".join(lines[first:last]),
            {"language": "python", "symbol": symbol},
            location={"line_start": first + 1, "line_end": last},
        )
        start = last
    if start < len(lines):
        builder.add(
            "code",
            "".join(lines[start:]),
            {"language": "python"},
            location={"line_start": start + 1, "line_end": len(lines)},
        )


def structured_tree(value: object, builder: Builder, language: str, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
            builder.heading(child)
            builder.add(
                "code",
                json.dumps({str(key): item}, ensure_ascii=False, indent=2),
                {"language": language, "path": child, "value": item, "key": str(key)},
            )
    else:
        builder.heading(path)
        builder.add(
            "code",
            json.dumps(value, ensure_ascii=False, indent=2),
            {"language": language, "path": path, "value": value},
        )


def xml_text(node: XmlTextNode) -> str:
    return "".join(n.text or "" for n in node.iter() if n.tag.endswith(("}t", "}v")))


def office(inspection: Inspection, builder: Builder) -> None:
    modality = inspection.profile.modality
    with zipfile.ZipFile(io.BytesIO(inspection.data)) as archive:
        if modality == "docx":
            root = XML.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            body = root.find("w:body", ns)
            if body is None:
                raise ValueError("DOCX body missing")
            for index, item in enumerate(body):
                location: dict[str, object] = {"element_path": f"word/document.xml/body/*[{index + 1}]"}
                if item.tag.endswith("}tbl"):
                    rows = [[xml_text(c) for c in r.findall("w:tc", ns)] for r in item.findall("w:tr", ns)]
                    builder.table(rows[0], rows[1:], location)
                elif item.tag.endswith("}p"):
                    text = xml_text(item)
                    style = item.find("w:pPr/w:pStyle", ns)
                    name = style.get(f"{{{ns['w']}}}val", "") if style is not None else ""
                    if name.lower().startswith("heading"):
                        builder.heading(text, int(name[-1]) if name[-1:].isdigit() else 1, location)
                    else:
                        builder.add(
                            "list" if item.find("w:pPr/w:numPr", ns) is not None else "paragraph",
                            text,
                            location=location,
                        )
                    if item.find(".//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath") is not None:
                        builder.result.warnings.append("Office equation retained as text; no LaTeX conversion")
        elif modality == "pptx":
            slides = sorted(
                (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                key=lambda n: int(n.rsplit("slide", 1)[1].split(".")[0]),
            )
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            presentation = XML.fromstring(archive.read("ppt/presentation.xml"))
            slide_ids = presentation.findall(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sldId")
            if slide_ids:
                relationships = {
                    n.get("Id"): n.get("Target", "")
                    for n in XML.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
                }
                ordered_slides: list[str] = []
                for slide_id in slide_ids:
                    target = relationships.get(
                        slide_id.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"), ""
                    )
                    target = target.lstrip("/") if target.startswith("/") else "ppt/" + target
                    if target not in slides or target in ordered_slides:
                        raise ValueError("Invalid presentation slide reference")
                    ordered_slides.append(target)
                slides = ordered_slides
            for number, name in enumerate(slides, 1):
                root = XML.fromstring(archive.read(name))
                builder.heading(f"Slide {number}", location={"slide": number})
                for item in root.iter():
                    if item.tag.endswith("}tbl"):
                        rows = [[xml_text(c) for c in r.findall("a:tc", ns)] for r in item.findall("a:tr", ns)]
                        builder.table(rows[0], rows[1:], {"slide": number})
                    elif item.tag.endswith("}sp"):
                        for paragraph in item.findall(".//a:p", ns):
                            builder.add("paragraph", xml_text(paragraph), location={"slide": number})
        else:
            ns = {
                "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            }
            strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = [xml_text(n) for n in XML.fromstring(archive.read("xl/sharedStrings.xml"))]
            relations = {
                n.get("Id"): n.get("Target") for n in XML.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            }
            workbook = XML.fromstring(archive.read("xl/workbook.xml"))
            for sheet in workbook.findall("s:sheets/s:sheet", ns):
                title = sheet.get("name", "Sheet")
                target = relations.get(sheet.get(f"{{{ns['r']}}}id"), "")
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                if ".." in Path(target).parts or target not in archive.namelist():
                    raise ValueError("Invalid worksheet reference")
                root = XML.fromstring(archive.read(target))
                cells: dict[int, dict[int, str]] = {}
                max_column = 0
                for row in root.findall("s:sheetData/s:row", ns):
                    number = int(row.get("r", "0"))
                    values: dict[int, str] = {}
                    for cell in row.findall("s:c", ns):
                        reference = cell.get("r", "")
                        match = re.fullmatch(r"([A-Z]+)([1-9]\d*)", reference)
                        if not match:
                            raise ValueError("Invalid spreadsheet cell")
                        column = 0
                        for char in match.group(1):
                            column = column * 26 + ord(char) - 64
                        if column > 16384 or number > 1_048_576:
                            raise ValueError("Spreadsheet dimensions exceed limits")
                        max_column = max(max_column, column)
                        value = cell.find("s:v", ns)
                        text = value.text or "" if value is not None else ""
                        if cell.get("t") == "s":
                            text = strings[int(text)]
                        elif cell.get("t") == "inlineStr":
                            text = xml_text(cell)
                        if cell.find("s:f", ns) is not None:
                            builder.result.warnings.append("Spreadsheet formulas use cached values; never executed")
                            if value is None:
                                raise ValueError("Spreadsheet formula has no cached value")
                        values[column] = text
                    if values:
                        cells[number] = values
                if cells:
                    builder.heading(title)
                    numbers = sorted(cells)
                    column_name = ""
                    column_number = max_column
                    while column_number:
                        column_number, remainder = divmod(column_number - 1, 26)
                        column_name = chr(65 + remainder) + column_name
                    rows = [[cells[n].get(c, "") for c in range(1, max_column + 1)] for n in numbers]
                    builder.table(
                        rows[0], rows[1:], {"sheet": title, "cell_range": f"A{numbers[0]}:{column_name}{numbers[-1]}"}
                    )
                    builder.result.blocks[-1].metadata["row_numbers"] = numbers[1:]
                    builder.result.blocks[-1].metadata["merged_ranges"] = [
                        n.get("ref") for n in root.findall("s:mergeCells/s:mergeCell", ns)
                    ]
        for name in archive.namelist():
            if "/media/" in name:
                builder.asset(
                    f"source:{builder.source.source_id}#archive/{name}",
                    "application/octet-stream",
                    {"element_path": name},
                )


def html(text: str, builder: Builder) -> None:
    dom = BeautifulSoup(text, "html.parser")
    for hidden in dom(["script", "style", "nav", "noscript"]):
        hidden.decompose()

    def emit(node: Tag) -> None:
        if node.name.startswith("h"):
            builder.heading(node.get_text(" ", strip=True), int(node.name[1:]))
        elif node.name == "table":
            rows = [
                [c.get_text(" ", strip=True) for c in r.find_all(["th", "td"], recursive=False)]
                for r in node.find_all("tr")
            ]
            builder.table(rows[0], rows[1:])
        elif node.name == "pre":
            builder.add("code", node.get_text(), {"language": "text"})
        elif node.name == "img":
            reference = str(node.get("src", ""))
            if reference:
                builder.asset(reference, "image/unknown")
                builder.add("caption", str(node.get("alt", "")))
        else:
            kind: BlockType = {"ul": "list", "ol": "list", "blockquote": "quote"}.get(node.name, "paragraph")  # type: ignore[assignment]
            builder.add(kind, node.get_text("\n", strip=True))

    blocks = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "table", "ul", "ol", "blockquote", "img"}
    containers = {"div", "section", "article", "main", "header", "footer", "aside", "address", "dl", "dt", "dd"}
    pending: list[str] = []

    def flush() -> None:
        builder.add("paragraph", "".join(pending).strip())
        pending.clear()

    def walk(parent: Tag) -> None:
        for node in parent.children:
            if isinstance(node, NavigableString) and not isinstance(node, (Comment, Doctype)):
                pending.append(str(node))
            elif isinstance(node, Tag):
                if node.name in blocks:
                    flush()
                    emit(node)
                    # Paragraphs/headings can contain assets; their text is already emitted once.
                    if node.name not in {"pre", "table", "ul", "ol", "blockquote", "img"}:
                        for asset in node.find_all("img"):
                            emit(asset)
                elif node.name == "br":
                    pending.append("\n")
                else:
                    if node.name in containers:
                        flush()
                    walk(node)
                    if node.name in containers:
                        flush()

    walk(dom)
    flush()


class NativeParser:
    name, tier = "native", 1

    def supports(self, inspection: Inspection) -> bool:
        return inspection.profile.modality not in {"image", "audio", "video"}

    def parse(self, inspection: Inspection, source: Source, config: PipelineConfig) -> Parsed:
        builder = Builder(source, self.name, config)
        modality, text = inspection.profile.modality, inspection.text or ""
        if modality in {"docx", "pptx", "xlsx"}:
            office(inspection, builder)
        elif modality == "pdf":
            for number, page in enumerate(inspection.pdf_text or [], 1):
                builder.add("paragraph", page, location={"page": number})
        elif modality == "markdown":
            uploaded = maybe_parse_concept(text, source.filename)
            if uploaded:
                builder.result.metadata["uploaded_okf"] = uploaded.frontmatter
                text = uploaded.body
            markdown(text, builder)
        elif modality == "html":
            html(text, builder)
        elif modality == "csv":
            rows = list(csv.reader(io.StringIO(text), strict=True))
            builder.table(rows[0], rows[1:], {"line_start": 1, "line_end": len(text.splitlines())})
        elif modality in {"json", "yaml"}:
            value = json.loads(text, object_pairs_hook=unique_mapping) if modality == "json" else load_yaml(text)
            value = _json_value(value, set(), [100000], 0)
            structured_tree(value, builder, modality)
        elif modality == "xml":
            root = XML.fromstring(text)
            if len(root) and root.text:
                builder.add("paragraph", root.text, {"path": f"/{root.tag}"})
            for index, item in enumerate(root):
                path = f"/{root.tag}/{item.tag}[{index + 1}]"
                builder.heading(path)
                builder.add("code", XML.tostring(item, encoding="unicode"), {"language": "xml", "path": path})
            if not len(root):
                builder.add("code", text, {"language": "xml", "path": f"/{root.tag}"})
        elif modality == "code":
            language = CODE_LANGUAGES[source.extension]
            if language == "python":
                python_code(text, builder)
            else:
                builder.add(
                    "code", text, {"language": language}, location={"line_start": 1, "line_end": len(text.splitlines())}
                )
                builder.result.warnings.append(
                    "Non-Python code preserved atomically; language AST adapter not configured"
                )
        elif modality == "email":
            message = BytesParser(policy=policy.default).parsebytes(inspection.data)
            builder.heading(str(message.get("Subject", source.filename)))
            builder.result.metadata["message_id"] = str(message.get("Message-ID", ""))
            builder.result.metadata["thread_id"] = str(message.get("References", message.get("Message-ID", "")))
            body = message.get_body(preferencelist=("plain", "html"))
            if body:
                if body.get_content_type() == "text/html":
                    html(str(body.get_content()), builder)
                else:
                    builder.add("paragraph", str(body.get_content()))
        elif modality == "transcript":
            cues = re.split(r"\n\s*\n", text)
            for cue in cues:
                time = re.search(r"(\d\d):(\d\d):(\d\d)[.,](\d+)\s*-->\s*(\d\d):(\d\d):(\d\d)[.,](\d+)", cue)
                if time:
                    parts = [int(x) for x in time.groups()]
                    start = parts[0] * 3600 + parts[1] * 60 + parts[2] + parts[3] / 1000
                    end = parts[4] * 3600 + parts[5] * 60 + parts[6] + parts[7] / 1000
                    builder.add(
                        "paragraph",
                        cue[time.end() :].strip(),
                        {"speaker": re.findall(r"<v ([^>]+)>", cue)},
                        location={"timestamp_start": start, "timestamp_end": end},
                    )
        elif modality == "log":
            groups = re.split(r"(?m)(?=^\d{4}-\d\d-\d\d[T ])", text)
            offset = 1
            for group in groups:
                lines = group.splitlines()
                builder.add(
                    "paragraph",
                    group,
                    {"trace_ids": re.findall(r"(?:trace|request)[_-]?id[=: ]+([\w-]+)", group)},
                    location={"line_start": offset, "line_end": offset + max(0, len(lines) - 1)},
                )
                offset += len(lines)
        else:
            offset = 1
            for paragraph in re.split(r"\n\s*\n", text):
                end = offset + max(0, len(paragraph.splitlines()) - 1)
                builder.add("paragraph", paragraph, location={"line_start": offset, "line_end": end})
                offset = end + 2
        return builder.result


class LayoutParser:
    name, tier = "layout", 2

    def supports(self, inspection: Inspection) -> bool:
        return inspection.profile.modality == "pdf"

    def parse(self, inspection: Inspection, source: Source, config: PipelineConfig) -> Parsed:
        import pdfplumber  # noqa: PLC0415

        builder = Builder(source, self.name, config)
        ordered_pairs = 0
        overlapping_pairs = 0
        with pdfplumber.open(io.BytesIO(inspection.data)) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                first_block = len(builder.result.blocks)
                tables = page.find_tables()
                table_boxes = [t.bbox for t in tables]
                words = [
                    w
                    for w in page.extract_words()
                    if not any(x0 <= w["x0"] <= x1 and y0 <= w["top"] <= y1 for x0, y0, x1, y1 in table_boxes)
                ]
                # A persistent wide gap identifies column bands; within each band use top-to-bottom order.
                starts = sorted({float(w["x0"]) for w in words})
                gaps = [(b - a, (a + b) / 2) for a, b in pairwise(starts)]
                boundary = max(gaps, default=(0, 0))
                split = boundary[1] if boundary[0] > page.width * 0.12 else None
                groups = [[w for w in words if split is None or w["x0"] < split]]
                if split is not None:
                    groups.append([w for w in words if w["x0"] >= split])
                for group in groups:
                    lines: dict[int, list[dict[str, object]]] = {}
                    for word in group:
                        lines.setdefault(round(word["top"] / 3), []).append(word)
                    paragraph: list[tuple[str, list[float]]] = []

                    def flush_paragraph(paragraph: list[tuple[str, list[float]]], page_number: int) -> None:
                        if not paragraph:
                            return
                        boxes = [box for _, box in paragraph]
                        builder.add(
                            "paragraph",
                            "\n".join(content for content, _ in paragraph),
                            {"line_bboxes": boxes},
                            location={
                                "page": page_number,
                                "bbox": [
                                    min(b[0] for b in boxes), min(b[1] for b in boxes),
                                    max(b[2] for b in boxes), max(b[3] for b in boxes),
                                ],
                            },
                        )
                        paragraph.clear()

                    for _, line in sorted(lines.items()):
                        ordered = sorted(line, key=lambda w: float(str(w["x0"])))
                        for left, right in pairwise(ordered):
                            ordered_pairs += 1
                            overlapping_pairs += float(str(left["x1"])) > float(str(right["x0"])) + 1
                        content = " ".join(str(w["text"]) for w in ordered)
                        bbox = [
                            min(float(str(w["x0"])) for w in line),
                            min(float(str(w["top"])) for w in line),
                            max(float(str(w["x1"])) for w in line),
                            max(float(str(w["bottom"])) for w in line),
                        ]
                        if paragraph:
                            previous, previous_box = paragraph[-1]
                            height = max(bbox[3] - bbox[1], previous_box[3] - previous_box[1])
                            soft_wrap = (
                                previous.rstrip()[-1:] not in {".", "!", "?", ";"}
                                and not re.match(r"^(?:[-*•]|\d+[.)])\s", content)
                                and -1 <= bbox[1] - previous_box[3] <= height * 0.7
                                and abs(bbox[0] - previous_box[0]) <= height * 2
                                and abs((bbox[3] - bbox[1]) - (previous_box[3] - previous_box[1])) <= height * 0.15
                            )
                            if not soft_wrap:
                                flush_paragraph(paragraph, number)
                        paragraph.append((content, bbox))
                    flush_paragraph(paragraph, number)
                for table in tables:
                    rows = [[v or "" for v in r] for r in table.extract()]
                    builder.table(rows[0], rows[1:], {"page": number, "bbox": table.bbox})
                for index, image in enumerate(page.images):
                    builder.asset(
                        f"source:{source.source_id}#page/{number}/image/{index}",
                        "image/unknown",
                        {"page": number, "bbox": [image["x0"], image["top"], image["x1"], image["bottom"]]},
                    )
                page_blocks = builder.result.blocks[first_block:]

                def reading_key(block: Block, column_boundary: float | None = split) -> tuple[int, float]:
                    box = block.provenance.bbox
                    return (
                        int(column_boundary is not None and box is not None and box[0] >= column_boundary),
                        box[1] if box else 0,
                    )

                builder.result.blocks[first_block:] = sorted(page_blocks, key=reading_key)
        builder.result.reading_order_confidence = 1 - overlapping_pairs / max(1, ordered_pairs)
        if inspection.profile.complex_layout:
            builder.result.warnings.append(
                "Reading order uses geometric column bands; spanning layouts may need review"
            )
        return builder.result


class OcrParser:
    name, tier = "ocr", 3

    def supports(self, inspection: Inspection) -> bool:
        return inspection.profile.modality in {"pdf", "image"}

    def parse(self, inspection: Inspection, source: Source, config: PipelineConfig) -> Parsed:
        try:
            return self._parse(inspection, source, config)
        except subprocess.SubprocessError as error:
            raise ValueError("OCR decoder failed or exceeded its time budget") from error

    def _parse(self, inspection: Inspection, source: Source, config: PipelineConfig) -> Parsed:
        tesseract = shutil.which("tesseract")
        renderer = shutil.which("pdftoppm")
        if not config.ocr_enabled or not tesseract or (inspection.pdf and not renderer):
            raise ValueError("OCR adapter requires configured Tesseract and PDF renderer")
        builder = Builder(source, self.name, config)
        with tempfile.TemporaryDirectory(prefix="fixflow-ocr-") as directory:
            root = Path(directory)
            source_path = root / ("input.pdf" if inspection.pdf else "input" + source.extension)
            source_path.write_bytes(inspection.data)
            source_path.chmod(0o600)
            pages = inspection.profile.page_count or 1
            for number in range(1, pages + 1):
                builder.asset(f"source:{source.source_id}#page/{number}", "image/unknown", {"page": number})
                if inspection.pdf and number not in inspection.profile.scanned_pages:
                    builder.add("paragraph", (inspection.pdf_text or [])[number - 1], location={"page": number})
                    continue
                image = source_path
                if inspection.pdf:
                    if renderer is None:
                        raise ValueError("PDF renderer unavailable")
                    prefix = root / "page"
                    # Fixed decoder argv; only server-created input/output paths.
                    subprocess.run(  # nosec B603
                        [
                            renderer,
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                            "-scale-to",
                            "2400",
                            "-png",
                            str(source_path),
                            str(prefix),
                        ],
                        check=True,
                        timeout=config.parser_timeout_seconds,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    image = prefix.with_suffix(".png")
                # Fixed decoder argv, private file and validated language.
                output = subprocess.run(  # nosec B603
                    [tesseract, str(image), "stdout", "-l", config.ocr_language, "tsv"],
                    check=True,
                    timeout=config.parser_timeout_seconds,
                    capture_output=True,
                )
                groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
                for row in csv.DictReader(io.StringIO(output.stdout.decode()), delimiter="\t"):
                    if row.get("text", "").strip() and float(row["conf"]) >= 0:
                        groups.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append(row)
                for rows in groups.values():
                    confidence = sum(float(r["conf"]) for r in rows) / (100 * len(rows))
                    x = min(int(r["left"]) for r in rows)
                    y = min(int(r["top"]) for r in rows)
                    right = max(int(r["left"]) + int(r["width"]) for r in rows)
                    bottom = max(int(r["top"]) + int(r["height"]) for r in rows)
                    builder.add(
                        "paragraph",
                        " ".join(r["text"] for r in rows),
                        location={"page": number, "bbox": [x, y, right, bottom]},
                        confidence=confidence,
                    )
        builder.result.warnings.append("OCR coordinates are rendered-image pixels; visual structures may need review")
        return builder.result


@dataclass
class CallbackParser:
    """Application-injected adapter for advanced parsers, transcription or a VLM; no fake default."""

    name: str
    tier: int
    accepts: Callable[[Inspection], bool]
    callback: Callable[[Inspection, Source, PipelineConfig], Parsed]

    def supports(self, inspection: Inspection) -> bool:
        return self.accepts(inspection)

    def parse(self, inspection: Inspection, source: Source, config: PipelineConfig) -> Parsed:
        return self.callback(inspection, source, config)
