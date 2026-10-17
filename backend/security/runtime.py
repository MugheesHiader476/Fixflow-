"""Runtime checks for model boundaries; secret values never enter errors or logs."""

from pydantic import BaseModel, SecretStr

from backend.config import get_settings
from backend.security.policy import validate_disclosure, validate_request


def configured_secrets() -> tuple[str, ...]:
    values: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
            if secret:
                values.append(secret)
        elif isinstance(value, BaseModel):
            for name in type(value).model_fields:
                collect(getattr(value, name))
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(get_settings())
    return tuple(sorted(set(values), key=len, reverse=True))


def request_policy(question: str) -> None:
    validate_request(question)
    validate_disclosure(question, configured_secrets())
