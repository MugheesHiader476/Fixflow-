"""Local TLS certificates are reusable, private and never overwrite unrelated files."""

import stat
from ipaddress import ip_address
from pathlib import Path

import pytest
from cryptography import x509

from scripts.local_https import local_certificate


def test_certificate_covers_loopback_hosts_and_is_reused(tmp_path: Path) -> None:
    key, certificate = local_certificate(tmp_path / "https")
    original = key.read_bytes(), certificate.read_bytes()
    parsed = x509.load_pem_x509_certificate(original[1])
    names = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["localhost"]
    assert set(names.get_values_for_type(x509.IPAddress)) == {ip_address("127.0.0.1"), ip_address("::1")}
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    assert local_certificate(key.parent) == (key, certificate)
    assert (key.read_bytes(), certificate.read_bytes()) == original


def test_incomplete_certificate_pair_is_preserved(tmp_path: Path) -> None:
    key = tmp_path / "localhost-key.pem"
    key.write_bytes(b"existing-private-file")
    with pytest.raises(ValueError, match="Both local HTTPS certificate files"):
        local_certificate(tmp_path)
    assert key.read_bytes() == b"existing-private-file"


def test_certificate_symlink_cannot_overwrite_a_private_file(tmp_path: Path) -> None:
    original = tmp_path / "existing.pem"
    original.write_bytes(b"private-file")
    directory = tmp_path / "https"
    directory.mkdir()
    (directory / "localhost-key.pem").symlink_to(original)
    with pytest.raises(ValueError, match="must not be symlinks"):
        local_certificate(directory)
    assert original.read_bytes() == b"private-file"


def test_mismatched_certificate_pair_is_rejected(tmp_path: Path) -> None:
    key, _ = local_certificate(tmp_path / "first")
    other_key, _ = local_certificate(tmp_path / "second")
    key.write_bytes(other_key.read_bytes())
    with pytest.raises(ValueError, match="do not match"):
        local_certificate(key.parent)
