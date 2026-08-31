from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def validate_fixed_destination(url: str, *, allow_private: bool, production: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("provider URL must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider URL cannot include credentials, query, or fragment")
    if production and parsed.scheme != "https":
        raise ValueError("production provider URL must use HTTPS")
    answers = socket.getaddrinfo(
        parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    )
    if not answers:
        raise ValueError("provider hostname did not resolve")
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0])
        if not address.is_global and not allow_private:
            raise ValueError("provider hostname resolves to a non-global address")
