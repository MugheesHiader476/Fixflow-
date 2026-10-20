"""Create a private localhost development certificate without changing system trust."""

import json
import os
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def local_certificate(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path, cert_path = directory / "localhost-key.pem", directory / "localhost.pem"
    if directory.is_symlink() or key_path.is_symlink() or cert_path.is_symlink():
        raise ValueError("Local HTTPS certificate paths must not be symlinks")
    if key_path.exists() or cert_path.exists():
        if not key_path.is_file() or not cert_path.is_file():
            raise ValueError("Both local HTTPS certificate files are required; preserve or move the existing pair")
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) != key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            raise ValueError("Local HTTPS certificate and key do not match")
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if "localhost" not in names.get_values_for_type(x509.DNSName):
            raise ValueError("Local HTTPS certificate must cover localhost")
        if certificate.not_valid_after_utc <= datetime.now(UTC):
            raise ValueError("Local HTTPS certificate expired; move the existing pair before generating a new one")
        key_path.chmod(0o600)
        return key_path, cert_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FixFlow localhost development")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1")), x509.IPAddress(ip_address("::1")),
        ]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    for path, payload in (
        (key_path, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )),
        (cert_path, certificate.public_bytes(serialization.Encoding.PEM)),
    ):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(payload)
    return key_path, cert_path


if __name__ == "__main__":
    try:
        private_key, public_certificate = local_certificate(Path(__file__).resolve().parents[1] / ".local/https")
    except (ValueError, OSError, x509.ExtensionNotFound):
        raise SystemExit(
            "Local HTTPS certificate setup failed. Check the private .local/https certificate pair."
        ) from None
    print(json.dumps({"key": str(private_key), "certificate": str(public_certificate)}))
