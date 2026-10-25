"""Normalize provider observations and bounded, digest-bound JUnit artifact archives."""

import hashlib
import io
import stat
import zipfile
from pathlib import PurePosixPath

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from backend.connectors.core import ConnectorError
from backend.schemas.github_evidence import GitHubTestCase

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 1024 * 1024
MAX_REPORT_ENTRIES = 64
MAX_TEST_CASES = 5000


def fields(value: dict[str, object], names: tuple[str, ...]) -> dict[str, object]:
    return {name: value[name] for name in names if name in value}


def junit_report(data: bytes, artifact_id: int, path: str) -> list[GitHubTestCase]:
    if len(data) > MAX_REPORT_BYTES:
        raise ConnectorError("too_large", "Test report exceeds the configured limit")
    try:
        text = data.decode("utf-8-sig")
        if "\x00" in text or "<!doctype" in text.casefold() or "<!entity" in text.casefold():
            raise ValueError("Unsupported XML declaration")
        root = ElementTree.fromstring(text)
    except (UnicodeError, ValueError, ElementTree.ParseError, DefusedXmlException) as error:
        raise ConnectorError("invalid_response", "Test report is not safe JUnit XML") from error
    if root.tag not in {"testsuite", "testsuites"}:
        raise ConnectorError("invalid_response", "Artifact is not a supported JUnit report")
    result: list[GitHubTestCase] = []
    queue = [(root, "", 0)]
    visited = 0
    report_hash = hashlib.sha256(data).hexdigest()
    while queue:
        element, suite, depth = queue.pop()
        visited += 1
        if visited > 50000 or depth > 64:
            raise ConnectorError("too_large", "Test report structure exceeds the configured limit")
        if element.tag == "testsuite":
            suite = element.attrib.get("name", "")
        if element.tag == "testcase":
            if not suite:
                raise ConnectorError("invalid_response", "Test report omitted a suite identity")
            statuses = {child.tag for child in element}
            if not statuses <= {"error", "failure", "skipped", "properties", "system-out", "system-err"}:
                raise ConnectorError("invalid_response", "Test report contains unsupported case result elements")
            declared_status = element.attrib.get("status")
            if declared_status is not None and declared_status not in {"run", "notrun", "skipped", "disabled"}:
                raise ConnectorError("invalid_response", "Test report contains an unsupported case execution status")
            if declared_status in {"notrun", "skipped", "disabled"}:
                statuses.add("skipped")
            declared_result = element.attrib.get("result")
            if declared_result is not None and declared_result not in {"completed", "suppressed", "skipped"}:
                raise ConnectorError("invalid_response", "Test report contains an unsupported case result status")
            if declared_result in {"suppressed", "skipped"}:
                statuses.add("skipped")
            status = (
                "error" if "error" in statuses else "failed" if "failure" in statuses else
                "skipped" if "skipped" in statuses else "passed"
            )
            result.append(GitHubTestCase.model_validate({
                "suite": suite,
                "name": element.attrib.get("name", ""),
                "classname": element.attrib.get("classname"),
                "status": status,
                "artifact_id": artifact_id,
                "report_path": path,
                "report_sha256": report_hash,
            }))
            if len(result) > MAX_TEST_CASES:
                raise ConnectorError("too_large", "Test report contains too many cases")
        queue.extend((child, suite, depth + 1) for child in reversed(element))
    if not result:
        raise ConnectorError("invalid_response", "Test report contains no executed test cases")
    for element in root.iter():
        if element.tag not in {"testsuite", "testsuites"}:
            continue
        cases = list(element.iter("testcase"))
        counts = {"tests": len(cases), "errors": 0, "failures": 0, "skipped": 0}
        for case in cases:
            child_types = {child.tag for child in case}
            if "error" in child_types:
                counts["errors"] += 1
            elif "failure" in child_types:
                counts["failures"] += 1
            elif (
                "skipped" in child_types or case.attrib.get("status") in {"notrun", "skipped", "disabled"}
                or case.attrib.get("result") in {"suppressed", "skipped"}
            ):
                counts["skipped"] += 1
        for key, count in counts.items():
            declared = element.attrib.get(key)
            if declared is not None and (
                len(declared) > 6 or not declared.isascii() or not declared.isdigit() or int(declared) != count
            ):
                raise ConnectorError("invalid_response", "JUnit summary does not match its actual case results")
    return result


def junit_archive(data: bytes, artifact_id: int, expected_digest: str) -> list[GitHubTestCase]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ConnectorError("too_large", "Test artifact exceeds the configured limit")
    if "sha256:" + hashlib.sha256(data).hexdigest() != expected_digest:
        raise ConnectorError("invalid_response", "Test artifact digest does not match its manifest")
    result: list[GitHubTestCase] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_REPORT_ENTRIES:
                raise ConnectorError("too_large", "Test artifact contains too many files")
            total = 0
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or len(entry.filename) > 500 or entry.flag_bits & 1
                    or stat.S_ISLNK(entry.external_attr >> 16)
                ):
                    raise ConnectorError("invalid_response", "Test artifact contains an unsafe archive member")
                total += entry.file_size
                if (
                    total > MAX_ARCHIVE_BYTES or entry.file_size > MAX_REPORT_BYTES
                    or entry.file_size > max(1, entry.compress_size) * 100
                ):
                    raise ConnectorError("too_large", "Expanded test artifact exceeds the configured limit")
                if entry.is_dir() or path.suffix.lower() != ".xml":
                    continue
                result.extend(junit_report(archive.read(entry), artifact_id, entry.filename))
                if len(result) > MAX_TEST_CASES:
                    raise ConnectorError("too_large", "Test artifact contains too many cases")
    except ConnectorError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError) as error:
        raise ConnectorError("invalid_response", "Test artifact archive cannot be read safely") from error
    identities = [(case.suite, case.name) for case in result]
    if not result or len(set(identities)) != len(identities):
        raise ConnectorError("invalid_response", "Test artifact has missing or duplicate test identities")
    return result
