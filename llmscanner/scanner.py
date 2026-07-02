"""Scan a subnet for open ports and identify LLM servers behind them."""
from __future__ import annotations

import asyncio
import errno
import ipaddress
from typing import Callable, Iterable, Optional

try:
    import resource  # Unix only (macOS/Linux)
except ImportError:  # pragma: no cover
    resource = None

from .detect import detect
from .models import ServerInfo

# Default ports used by common local LLM servers.
DEFAULT_PORTS = [
    80,     # generic / reverse proxies
    1234,   # LM Studio
    3000,   # TGI / misc
    4000,   # LiteLLM proxy
    5000,   # misc
    7860,   # gradio / text-generation-webui
    8000,   # vLLM (default)
    8001,   # vLLM secondary
    8080,   # llama.cpp / LocalAI / TGI
    8888,   # misc
    9000,   # misc
    11434,  # Ollama
    23333,  # koboldcpp
    30000,  # SGLang (default)
]


def raise_fd_limit(target: int = 4096) -> None:
    """Raise the open-file soft limit (macOS defaults to 256).

    Without this, scanning at high concurrency exhausts file descriptors and
    open ports get silently reported as closed.
    """
    if resource is None:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = target if hard == resource.RLIM_INFINITY else min(hard, target)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except Exception:
        pass


async def _port_open(host: str, port: int, timeout: float) -> bool:
    # Retry once on fd exhaustion so a busy moment isn't mistaken for "closed".
    for attempt in range(2):
        try:
            fut = asyncio.open_connection(host, port)
            _reader, writer = await asyncio.wait_for(fut, timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except OSError as e:
            if e.errno in (errno.EMFILE, errno.ENFILE) and attempt == 0:
                await asyncio.sleep(0.1)
                continue
            return False
        except Exception:
            return False
    return False


async def scan_network(subnet: str, ports: Iterable[int], *, timeout: float = 1.0,
                       concurrency: int = 256,
                       progress_cb: Optional[Callable[[int, int], None]] = None
                       ) -> list[tuple[str, int]]:
    """Return a list of (host, port) tuples with an open TCP port."""
    raise_fd_limit()
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()] or [str(net.network_address)]
    ports = list(ports)
    total = len(hosts) * len(ports)
    sem = asyncio.Semaphore(concurrency)
    open_pairs: list[tuple[str, int]] = []
    done = 0

    async def check(host: str, port: int):
        nonlocal done
        async with sem:
            ok = await _port_open(host, port, timeout)
        if ok:
            open_pairs.append((host, port))
        done += 1
        if progress_cb:
            progress_cb(done, total)

    await asyncio.gather(*(check(h, p) for h in hosts for p in ports))
    return open_pairs


async def detect_many(pairs: Iterable[tuple[str, int]], *, timeout: float = 4.0,
                      concurrency: int = 64) -> list[ServerInfo]:
    """Fingerprint each (host, port); keep only likely LLM servers."""
    sem = asyncio.Semaphore(concurrency)

    async def one(host: str, port: int) -> ServerInfo:
        async with sem:
            return await detect(host, port, timeout=timeout)

    results = await asyncio.gather(*(one(h, p) for h, p in pairs))
    return [r for r in results if r and (r.openai_compatible or r.server_type != "unknown")]
