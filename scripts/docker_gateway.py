"""Prepare Docker's public listener; only loopback HTTPS gets a development certificate."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from scripts.local_https import local_certificate


def gateway_configuration(public_url: str) -> tuple[str, bool]:
    origin = urlsplit(public_url)
    local = origin.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        origin.scheme not in {"http", "https"}
        or (origin.scheme == "http" and origin.hostname not in {"localhost", "127.0.0.1"})
        or not re.fullmatch(r"(?:[A-Za-z0-9][A-Za-z0-9.-]*|\[::1\])(?::[0-9]{1,5})?", origin.netloc)
        or origin.path not in {"", "/"}
        or origin.query or origin.fragment
        or any(character.isspace() for character in public_url)
        or (origin.port is not None and not 1 <= origin.port <= 65535)
    ):
        raise ValueError("CONNECTORS__PUBLIC_URL must be a fixed HTTP(S) origin; HTTP is loopback-only")
    tls = local and origin.scheme == "https"
    listener = "listen 3000;"
    if tls:
        listener = f"""listen 3000 ssl;
        ssl_certificate /gateway/localhost.pem;
        ssl_certificate_key /gateway/localhost-key.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:TLS:1m;
        ssl_session_tickets off;
        # Old HTTP bookmarks reach the new origin without replaying a write or OAuth query.
        error_page 497 =303 https://{origin.netloc}/connectors;"""
    return f"""worker_processes auto;
pid /tmp/fixflow-gateway.pid;
# Nginx error records can contain full callback query strings. Keep those private.
error_log /dev/null;
events {{ worker_connections 1024; }}
http {{
    log_format safe '$request_method $uri $status';
    access_log /dev/stdout safe;
    client_body_temp_path /tmp/client_body;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    resolver 127.0.0.11 valid=10s ipv6=off;
    map $http_upgrade $connection_upgrade {{ default upgrade; '' ''; }}
    upstream fixflow_frontend {{
        zone fixflow_frontend 64k;
        server frontend:3000 resolve;
        keepalive 32;
    }}
    server {{
        listen 127.0.0.1:8081;
        access_log off;
        location = /health {{ return 200 'ok'; }}
    }}
    server {{
        {listener}
        server_name {origin.hostname};
        server_tokens off;
        client_max_body_size 52m;
        client_body_timeout 30s;
        location / {{
            proxy_pass http://fixflow_frontend;
            proxy_http_version 1.1;
            proxy_set_header Host {origin.netloc};
            proxy_set_header X-Forwarded-Host {origin.netloc};
            proxy_set_header X-Forwarded-Proto {origin.scheme};
            proxy_set_header X-Forwarded-Port {origin.port or (443 if origin.scheme == 'https' else 80)};
            proxy_set_header X-Forwarded-For '';
            proxy_set_header Forwarded '';
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            # Preserve Origin for the application's fixed-origin write check.
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_connect_timeout 3s;
            proxy_read_timeout 190s;
            proxy_send_timeout 190s;
            proxy_next_upstream off;
        }}
    }}
}}
""", tls


def prepare_gateway(directory: Path, public_url: str) -> bool:
    configuration, tls = gateway_configuration(public_url)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = directory / "nginx.conf"
    if directory.is_symlink() or config_path.is_symlink():
        raise ValueError("Gateway paths must not be symlinks")
    directory.chmod(0o700)
    if tls:
        local_certificate(directory)
    config_path.write_text(configuration)
    config_path.chmod(0o600)
    return tls


if __name__ == "__main__":
    gateway_directory = Path("/gateway")
    try:
        local_tls = prepare_gateway(
            gateway_directory, os.environ.get("CONNECTORS__PUBLIC_URL", "http://localhost:3000")
        )
        # Only the unprivileged Nginx user can read this private, persistent volume.
        for path in (gateway_directory, *gateway_directory.iterdir()):
            if path.is_symlink():
                raise ValueError("Gateway paths must not be symlinks")
            os.chown(path, 101, 101)
    except (ValueError, OSError):
        raise SystemExit("Docker gateway setup failed. Check the public origin and private gateway volume.") from None
    message = (
        "with localhost HTTPS; accept its development certificate in your browser."
        if local_tls else "for HTTP; public deployments require an outer TLS reverse proxy."
    )
    print("Docker gateway ready " + message)
