import pytest

from scripts.load_test import validated_database


@pytest.mark.parametrize("value", [
    "postgresql://user@127.0.0.1/application", "postgresql://user@remote.example/application_test",
    "sqlite:///application_test", "postgresql://user@localhost/application_test?host=remote.example",
])
def test_load_database_guard_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(ValueError):
        validated_database(value)


def test_load_database_guard_normalizes_postgresql_driver() -> None:
    assert validated_database("postgresql://user@127.0.0.1:55433/qa_test").startswith("postgresql+asyncpg://")
