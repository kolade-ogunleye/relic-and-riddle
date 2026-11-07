"""
src/url_guard.py
================
Server-side request forgery guard for URLs that come from untrusted data (QF-44): the pages a Wikipedia article
cites (src/free_evidence.page_text) and the image files museum and Commons searches name (commons.download_jpeg,
image_gen._download_reference, footage.download).

Every hop is checked before it is requested: HTTPS on the default port, a host that is neither a private, loopback,
link-local or otherwise non-public address nor a local name, and (per caller) one of the provider's hosts.
Redirects are never followed by the HTTP library; follow() takes each Location by hand and checks it the same way.
A host name is resolved and refused when any of its addresses is not public (an internal name, or a public name
pointed at 127.0.0.1). The library resolves again when it connects, so a name that changes its answer between the
two lookups is not caught here; the fixed provider hosts and HTTPS certificate checks cover the image downloads.
"""

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

MAX_HOPS = 5
# Names that only mean something on a local network (RFC 6761/6762/8375 and common internal suffixes).
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp", ".localdomain")
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")


class RefusedURL(ValueError):
    """A URL (or a redirect hop) the guard will not request."""


def _address(text: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(text.strip("[]").split("%")[0])
    except ValueError:
        return None


def _not_public(addr: ipaddress._BaseAddress) -> bool:
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return not addr.is_global or addr.is_multicast or addr.is_reserved or addr.is_unspecified


def _addresses(host: str) -> list[str]:
    """Every address the host name resolves to; [] when it does not resolve (the request then fails on its own)."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]
    except (OSError, UnicodeError):
        return []


def public_host(host: str, resolve: bool = True) -> bool:
    """Whether `host` is a public internet host: not empty, not a local name, not an address (in any spelling) in a
    private, loopback, link-local, shared, reserved or multicast range, and (with `resolve`) not a name that
    resolves to one."""
    host = (host or "").strip().rstrip(".").lower()
    if not host or host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return False
    addr = _address(host)
    if addr is not None:
        return not _not_public(addr)
    labels = host.split(".")
    if len(labels) < 2 or all(_NUMERIC_LABEL.match(label) for label in labels):
        return False  # a bare intranet name, or an address in decimal, octal or hex ("2130706433", "0x7f.1")
    if resolve:
        return not any(_not_public(a) for a in (_address(x) for x in _addresses(host)) if a is not None)
    return True


def checked(url: str, host_allowed: Callable[[str], bool], resolve: bool = True) -> bool:
    """HTTPS, default port, no credentials in the URL, a host `host_allowed` accepts and public_host() accepts."""
    try:
        parsed = urlparse(url or "")
        port = parsed.port
    except ValueError:
        return False
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and port in (None, 443) and not parsed.username and not parsed.password
            and host_allowed(host) and public_host(host, resolve=resolve))


def follow(url: str, request: Callable[..., object], allowed: Callable[[str], bool] | None = None,
           max_hops: int = MAX_HOPS, **kwargs):
    """The response for `url`, following at most `max_hops` redirects by hand: each hop must pass `allowed` (default:
    checked() with any public host) before `request(hop, allow_redirects=False, **kwargs)` is made. Raises
    RefusedURL for a refused hop or too many redirects."""
    allowed = allowed or (lambda u: checked(u, lambda host: True))
    for _ in range(max_hops + 1):
        if not allowed(url):
            parsed = urlparse(url or "")
            raise RefusedURL(f"refusing to request {parsed.scheme}://{parsed.hostname}")
        resp = request(url, allow_redirects=False, **kwargs)
        if not getattr(resp, "is_redirect", False):
            return resp
        location = (getattr(resp, "headers", None) or {}).get("Location", "")
        close = getattr(resp, "close", None)
        if callable(close):
            close()
        url = urljoin(url, location)
    raise RefusedURL("too many redirects")
