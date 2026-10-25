"""Bounded deterministic checks, not a semantic classifier or universal DLP engine."""

import json
import re
import unicodedata
from collections.abc import Iterator

POLICY_VERSION = "proofos-workspace-2026-10-25.1"
REDACTED = "[REDACTED_SECRET]"

# Deliberately narrow signatures: ordinary discussion of privacy/security is allowed.
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[\s\S]*?"
    r"(?:-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|\Z)"
    r"|\b(?:xai-|sk-(?:proj-)?)[A-Za-z0-9_-]{20,}"
    r"|\bAIza[A-Za-z0-9_-]{35}\b"
    r"|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"
    r"|\bxox[baprs]-[A-Za-z0-9-]{15,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~-]{20,}",
)
ASSIGNMENT = re.compile(
    r"(?i:(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password))"
    r"\s*[:=]\s*[\"']?(?P<value>[^\s\"',;<>]{12,})"
)
PLACEHOLDERS = frozenset({
    "your_api_key_here", "your_key_here", "paste_your_key_here", "paste_api_key_here",
    "example_password", "replace_this_value", REDACTED.casefold(),
})
DIRECT_ABUSE = re.compile(
    r"^(?:(?:please|can you|could you|i want you to)\s+)*"
    r"(?:"
    r"(?:show|reveal|print|dump|repeat|give me)\s+(?:(?:your|the|hidden|internal|full|exact|original)\s+)*"
    r"(?:system prompt|developer instructions|hidden reasoning|chain of thought)\b"
    r"|(?:steal|exfiltrate|harvest)\b.{0,100}\b(?:passwords?|credentials?|tokens?|private data|personal data)\b"
    r"|(?:bypass|disable)\s+(?:(?:all|the|your|my)\s+)*"
    r"(?:authentication|authorization|ownership|permissions?|access controls?|rate limits?)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)


class PolicyViolation(ValueError):
    """Never include rejected source text, credentials or model arguments in an error."""


def placeholder(value: str) -> bool:
    return value.rstrip(".?!").casefold() in PLACEHOLDERS


def validate_text(value: str, *, maximum: int = 4000) -> str:
    value = value.strip()
    if not value or len(value) > maximum:
        raise PolicyViolation("Use a nonblank request within the text limit")
    if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\r\t" for char in value):
        raise PolicyViolation("Request contains unsupported control characters")
    if len(value.encode("utf-8")) > maximum * 4:
        raise PolicyViolation("Request exceeds the encoded text limit")
    return value


def inspect_text(value: str) -> str:
    # Inspect compatibility forms and invisible separators without changing stored evidence.
    if value.isascii():
        return value
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Cf")


def validate_request(value: str) -> None:
    normalized = inspect_text(validate_text(value))
    if DIRECT_ABUSE.search(normalized):
        raise PolicyViolation("I cannot help expose internal instructions, steal data or bypass access controls")
    validate_disclosure(value)


def strings(value: object, depth: int = 0) -> Iterator[str]:
    if depth > 32:
        raise PolicyViolation("Workflow content exceeds the nesting limit")
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item, depth + 1)


def validate_disclosure(value: object, secrets: tuple[str, ...] = ()) -> None:
    for item in strings(value):
        normalized = inspect_text(item)
        assigned = any(not placeholder(match["value"]) for match in ASSIGNMENT.finditer(normalized))
        if SECRET.search(normalized) or assigned or any(secret and secret in normalized for secret in secrets):
            raise PolicyViolation("Credentials or authentication secrets cannot be sent to a model or disclosed")


def redact(value: object, secrets: tuple[str, ...] = (), depth: int = 0) -> object:
    if depth > 32:
        raise PolicyViolation("Workflow content exceeds the nesting limit")
    if isinstance(value, str):
        cleaned = SECRET.sub(REDACTED, value)
        cleaned = ASSIGNMENT.sub(
            lambda match: match[0] if placeholder(match["value"]) else REDACTED, cleaned
        )
        for secret in secrets:
            if secret:
                cleaned = cleaned.replace(secret, REDACTED)
        # Obfuscated recognizable secrets are blocked instead of changing canonical offsets.
        validate_disclosure(cleaned, secrets)
        return cleaned
    if isinstance(value, dict):
        return {str(key): redact(item, secrets, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets, depth + 1) for item in value]
    return value


def outbound_email(value: dict[str, object], secrets: tuple[str, ...] = ()) -> None:
    validate_disclosure(value, secrets)
    # No encoded HTML/remote image rendering in the app or outbound mail.
    for key in ("subject", "body"):
        text = value.get(key)
        if not isinstance(text, str):
            raise PolicyViolation("Email content is invalid")
        validate_text(text, maximum=12000)


def serialized_size(value: object, maximum: int = 512 * 1024) -> None:
    if len(json.dumps(value, ensure_ascii=False).encode()) > maximum:
        raise PolicyViolation("Workflow content exceeds the processing limit")
