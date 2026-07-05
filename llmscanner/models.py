"""Plain data containers shared across the app."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ServerInfo:
    """Result of fingerprinting a single host:port."""
    host: str
    port: int
    scheme: str = "http"
    base_path: str = ""
    server_type: str = "unknown"        # vllm / sglang / ollama / llamacpp / tgi / lmstudio / openai-compatible
    openai_compatible: bool = False
    reachable: bool = False
    models: list[str] = field(default_factory=list)
    version: Optional[str] = None
    note: str = ""

    @property
    def url(self) -> str:
        default_port = {"http": 80, "https": 443}.get(self.scheme)
        host = f"[{self.host}]" if (":" in self.host and not self.host.startswith("[")) else self.host
        origin = (f"{self.scheme}://{host}" if self.port == default_port
                  else f"{self.scheme}://{host}:{self.port}")
        return origin + self.base_path


@dataclass
class RequestResult:
    """Metrics for a single generation request."""
    ok: bool
    ttft: float = 0.0               # seconds to first token
    total_time: float = 0.0         # seconds, whole request
    prompt_tokens: int = 0
    completion_tokens: int = 0
    output_tps: float = 0.0         # decode throughput, tokens/sec
    error: str = ""
    text: str = ""
    est_tokens: bool = False        # token counts estimated (server sent no usage)
    finish_reason: str = ""         # "stop" / "length" / "" — why generation ended
    stream_chunks: int = 0          # content-or-reasoning-bearing SSE chunks — independent token count
    logprob_avg: Optional[float] = None  # mean logprob of generated tokens (None if unavailable)
    reasoning: str = ""             # reasoning_content / <think> text (reasoning models)
