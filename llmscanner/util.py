"""Small shared helpers (no third-party deps)."""
from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

DEFAULT_SCHEME_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class Target:
    """A fully-resolved connection target for an OpenAI-compatible server.

    `base_path` is an optional prefix that sits in front of the `/v1/...` routes
    (e.g. a reverse proxy mounted at `/api`); it is empty for the common case.
    """
    scheme: str
    host: str
    port: int
    base_path: str = ""

    @property
    def origin(self) -> str:
        # Bracket IPv6 literals (urlsplit strips the []), else the port colon
        # merges with the address colons into an invalid URL.
        host = f"[{self.host}]" if (":" in self.host and not self.host.startswith("[")) else self.host
        # Drop the port when it's the scheme default — cleaner and avoids the odd
        # TLS/proxy setup that dislikes an explicit :443.
        if self.port == DEFAULT_SCHEME_PORTS.get(self.scheme):
            return f"{self.scheme}://{host}"
        return f"{self.scheme}://{host}:{self.port}"

    @property
    def base_url(self) -> str:
        return self.origin + self.base_path


def _int_or_none(v) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _looks_like_domain(host: str) -> bool:
    """True for a public-style hostname (has a dot, not an IP literal, not localhost)."""
    if not host or host == "localhost":
        return False
    try:
        ipaddress.ip_address(host)
        return False  # an IP literal is not a domain name
    except ValueError:
        return "." in host


def is_private_host(host: str) -> bool:
    """True when `host` is unambiguously on this machine or a local network.

    Used to decide TLS verification: a local LLM server routinely has a
    self-signed certificate, so verification has to be off to reach it at all. A
    public host does not get that exemption — we send an API key in the
    Authorization header, and skipping verification there would hand that key to
    anyone able to intercept the connection.
    """
    h = (host or "").strip().strip("[]").lower()
    if not h:
        return False
    if h == "localhost" or h.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def tls_verify_for(host: str) -> bool:
    """Whether httpx should verify TLS certificates when talking to `host`.

    Off for a private/loopback host (self-signed certs are the norm there), on
    for anything public. `LLMSCANNER_INSECURE_TLS=1` forces it off everywhere —
    the escape hatch for a public host with a self-signed certificate. Nothing
    can turn verification off for a public host implicitly.
    """
    if os.environ.get("LLMSCANNER_INSECURE_TLS", "").strip().lower() in ("1", "true", "yes"):
        return False
    return not is_private_host(host)


def _clean_base_path(path: str) -> str:
    """Normalise a URL path into a prefix that the client can append `/v1/...` to.

    The client always adds `/v1/...` itself, so a trailing `/v1` the user pasted
    is stripped; so is any trailing slash.
    """
    p = (path or "").rstrip("/")
    if p.lower().endswith("/v1"):
        p = p[:-3]
    return p


def split_host_field(raw: str):
    """Parse whatever the user typed into the Host field.

    Accepts a bare host (`apirouter.itteam.eu`), `host:port`, or a full URL
    (`https://host/v1`). Returns (scheme|None, host, port|None, base_path); a
    None scheme/port means the input did not pin it down.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "", None, ""
    # urlsplit needs an authority; add one when the user typed no scheme.
    work = raw if "://" in raw else "//" + raw
    parts = urlsplit(work)
    scheme = parts.scheme or None
    # Fall back to the raw token only for a truly bare host (no delimiters), so
    # junk like ":8000" doesn't become the hostname.
    host = parts.hostname or (raw if (":" not in raw and "/" not in raw) else "")
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, host, port, _clean_base_path(parts.path)


def resolve_target(raw_host: str, raw_port=None) -> Target:
    """Turn the Host (+ Port) field into one best-guess Target.

    Precedence: anything explicit in the Host string wins over the Port field.
    A bare public hostname defaults to HTTPS on 443 (the Port field is meant for
    the local-server workflow); a bare IP/localhost uses the Port field over HTTP.
    """
    raw = (raw_host or "").strip()
    scheme, host, port, base_path = split_host_field(raw)
    url_like = ("://" in raw) or ("/" in raw) or (port is not None)
    is_domain = _looks_like_domain(host)

    if scheme is None:
        scheme = "https" if is_domain else "http"
    if port is None:
        if url_like or is_domain:
            port = DEFAULT_SCHEME_PORTS[scheme]
        else:
            field = _int_or_none(raw_port)
            port = field if field is not None else DEFAULT_SCHEME_PORTS[scheme]
    return Target(scheme, host, port, base_path)


def candidate_targets(raw_host: str, raw_port=None) -> list[Target]:
    """Ordered targets to probe when auto-detecting.

    The primary guess comes first; when the user didn't pin a scheme we also try
    the other scheme, plus the Port-field port over HTTP for a bare public host.
    """
    raw = (raw_host or "").strip()
    scheme_in, host, port_in, base_path = split_host_field(raw)
    primary = resolve_target(raw, raw_port)
    cands = [primary]

    if scheme_in is None:
        alt = "http" if primary.scheme == "https" else "https"
        alt_port = (DEFAULT_SCHEME_PORTS[alt]
                    if primary.port in DEFAULT_SCHEME_PORTS.values() else primary.port)
        cands.append(Target(alt, host, alt_port, base_path))
        field = _int_or_none(raw_port)
        if _looks_like_domain(host) and port_in is None and field not in (None, 80, 443):
            cands.append(Target("http", host, field, base_path))

    seen, out = set(), []
    for t in cands:
        key = (t.scheme, t.host, t.port, t.base_path)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def local_ip() -> str:
    """Best-effort local IPv4 address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are actually sent; this just picks the right interface.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def default_subnet(prefix: int = 24) -> str:
    """Guess the local subnet in CIDR form, e.g. '192.168.1.0/24'."""
    ip = local_ip()
    try:
        iface = ipaddress.ip_interface(f"{ip}/{prefix}")
        return str(iface.network)
    except Exception:
        return "192.168.1.0/24"


def approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) used only as a fallback."""
    return max(1, len(text) // 4)


MAX_PORT = 65535
MAX_SCAN_PORTS = 4096   # a scan opens a socket per (host, port) — keep it sane


def parse_ports(spec: str, default: list[int]) -> list[int]:
    """Parse '8000,8080,30000' or '8000-8010' into a list of ports.

    Every port is validated against 1..65535 and the total is capped: a typo
    like '1-5000000' would otherwise build a multi-million entry list and then
    try to open that many sockets, wedging the scan.
    """
    if not spec:
        return list(default)

    def one(tok: str) -> int:
        n = int(tok)
        if not 1 <= n <= MAX_PORT:
            raise ValueError(f"port {n} is outside 1..{MAX_PORT}")
        return n

    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part.lstrip("-"):
            a, b = part.split("-", 1)
            lo, hi = one(a.strip()), one(b.strip())
            if lo > hi:
                raise ValueError(f"port range {lo}-{hi} runs backwards")
            out.extend(range(lo, hi + 1))
        else:
            out.append(one(part))
        if len(out) > MAX_SCAN_PORTS:
            raise ValueError(f"too many ports ({len(out)}); the limit is {MAX_SCAN_PORTS}")
    return out
