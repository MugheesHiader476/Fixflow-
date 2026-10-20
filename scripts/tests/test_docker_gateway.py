"""Docker TLS must match the public origin without overriding browser write origins."""

import stat
from pathlib import Path

import pytest

from scripts.docker_gateway import gateway_configuration, prepare_gateway


def test_local_https_is_private_and_reuses_its_certificate(tmp_path: Path) -> None:
    directory = tmp_path / "gateway"
    directory.mkdir(mode=0o755)
    assert prepare_gateway(directory, "https://localhost:3000")
    key, certificate = directory / "localhost-key.pem", directory / "localhost.pem"
    original = key.read_bytes(), certificate.read_bytes()
    assert prepare_gateway(directory, "https://localhost:3000/")
    assert (key.read_bytes(), certificate.read_bytes()) == original
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    configuration = (directory / "nginx.conf").read_text()
    assert "listen 3000 ssl;" in configuration
    assert "proxy_set_header Host localhost:3000;" in configuration
    assert "proxy_set_header X-Forwarded-Proto https;" in configuration
    assert "proxy_set_header Origin" not in configuration
    assert "proxy_next_upstream off;" in configuration
    assert "error_page 497 =303 https://localhost:3000/connectors;" in configuration
    assert "$request_uri" not in configuration and "$args" not in configuration


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000", "https://app.example.test"])
def test_plain_http_and_outer_production_tls_do_not_get_a_self_signed_certificate(tmp_path: Path, origin: str) -> None:
    assert not prepare_gateway(tmp_path, origin)
    assert not (tmp_path / "localhost-key.pem").exists()
    configuration, _ = gateway_configuration(origin)
    assert "listen 3000;" in configuration
    assert "ssl_certificate" not in configuration


@pytest.mark.parametrize("origin", [
    "invalid URL", "http://app.example.test", "https://user:password@localhost:3000",
    "https://localhost:3000/path", "https://localhost:3000?query=1", "https://localhost:3000#fragment",
    "https://localhost:3000;malicious", "https://localhost:99999", "https://localhost:0",
    "https://localhost:3000\n", "https://localhost:3000\nserver evil", "https://$http_host",
])
def test_untrusted_public_configuration_cannot_enter_nginx(tmp_path: Path, origin: str) -> None:
    with pytest.raises(ValueError):
        prepare_gateway(tmp_path, origin)
    assert not (tmp_path / "nginx.conf").exists()


def test_gateway_cannot_overwrite_a_symlink(tmp_path: Path) -> None:
    original = tmp_path / "private.txt"
    original.write_text("preserved")
    directory = tmp_path / "gateway"
    directory.mkdir()
    (directory / "nginx.conf").symlink_to(original)
    with pytest.raises(ValueError, match="must not be symlinks"):
        prepare_gateway(directory, "https://localhost:3000")
    assert original.read_text() == "preserved"
