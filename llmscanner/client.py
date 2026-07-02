"""Async client for OpenAI-compatible LLM servers, with timing."""
from __future__ import annotations

import json
import time
from typing import Optional

import httpx

from .models import RequestResult
from .util import Target, approx_tokens


class LLMClient:
    """Talks to an OpenAI-compatible /v1 endpoint and measures speed.

    Works with vLLM, SGLang, llama.cpp server, Ollama (/v1), TGI's
    OpenAI route, LM Studio, LocalAI, etc.
    """

    def __init__(self, host: str, port: int, *, api_key: str = "EMPTY",
                 scheme: str = "http", timeout: float = 120.0, endpoint: str = "chat",
                 base_path: str = ""):
        self.host = host
        self.port = port
        self.scheme = scheme
        self.base_path = base_path
        self.base_url = Target(scheme, host, port, base_path).base_url
        self.api_key = api_key or "EMPTY"
        self.timeout = timeout
        self.endpoint = endpoint  # "chat" or "completions"

    @classmethod
    def from_target(cls, target: Target, **kwargs) -> "LLMClient":
        return cls(target.host, target.port, scheme=target.scheme,
                   base_path=target.base_path, **kwargs)

    def _http(self, **kwargs) -> httpx.AsyncClient:
        # verify=False: local LLM servers commonly use self-signed TLS certs.
        return httpx.AsyncClient(timeout=self.timeout, verify=False, **kwargs)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def list_models(self) -> list[str]:
        async with self._http() as c:
            r = await c.get(f"{self.base_url}/v1/models", headers=self._headers())
            r.raise_for_status()
            data = r.json()
            return [m.get("id", "?") for m in data.get("data", [])]

    async def model_max_len(self, model: Optional[str] = None) -> Optional[int]:
        """Return the server-advertised max context length, if exposed.

        vLLM includes `max_model_len` in each /v1/models entry; this lets the
        limits test report the real limit without sending oversized requests.
        """
        keys = ("max_model_len", "max_context_length", "context_length",
                "max_seq_len", "max_position_embeddings")
        try:
            async with self._http() as c:
                r = await c.get(f"{self.base_url}/v1/models", headers=self._headers())
                r.raise_for_status()
                data = r.json().get("data", [])
        except Exception:
            return None
        ordered = [m for m in data if model is None or m.get("id") == model] or data
        for m in ordered:
            for k in keys:
                v = m.get(k)
                if isinstance(v, int) and v > 0:
                    return v
        return None

    def _payload(self, model, prompt, max_tokens, temperature, system, include_usage,
                 force_output=False):
        if self.endpoint == "completions":
            url = f"{self.base_url}/v1/completions"
            payload = {
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
        else:
            url = f"{self.base_url}/v1/chat/completions"
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
        if force_output:
            # Force exactly `max_tokens` of decoding: ignore an early end-of-text
            # and require the full length. Without this a model often stops after
            # ~1 token (especially on large filler prompts), so decode throughput
            # would be measured on near-zero output and rows wouldn't be comparable.
            # vLLM/SGLang extension; dropped automatically if the server rejects it.
            payload["ignore_eos"] = True
            payload["min_tokens"] = max_tokens
        if include_usage:
            # vLLM/SGLang return accurate token counts in the final SSE chunk.
            payload["stream_options"] = {"include_usage": True}
        return url, payload

    async def generate(self, *, model: str, prompt: str, max_tokens: int = 128,
                       temperature: float = 0.0, system: Optional[str] = None,
                       force_output: bool = False) -> RequestResult:
        """Stream one completion and return timing/throughput metrics.

        `force_output` requests exactly `max_tokens` of output (ignore_eos /
        min_tokens) so throughput is measured on a fixed decode length.
        """
        # Attempts in order; on a retriable 400/422 we drop the fields most likely
        # to be unsupported — first stream_options, then ignore_eos/min_tokens.
        combos = [(force_output, True), (force_output, False)]
        if force_output:
            combos += [(False, True), (False, False)]
        for idx, (fo, iu) in enumerate(combos):
            try:
                return await self._stream_once(
                    model=model, prompt=prompt, max_tokens=max_tokens,
                    temperature=temperature, system=system,
                    include_usage=iu, force_output=fo,
                )
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                body = ""
                try:
                    body = e.response.text[:300]
                except Exception:
                    pass
                # Retry a validation 400/422 with fewer optional fields — never an
                # un-retriable context-length error.
                ctx_err = any(s in body.lower() for s in (
                    "context length", "input tokens", "maximum context",
                    "reduce the length", "too long", "longer than", "max_model_len"))
                if code in (400, 422) and not ctx_err and idx < len(combos) - 1:
                    continue
                return RequestResult(ok=False, error=f"HTTP {code}: {body}")
            except Exception as e:
                return RequestResult(ok=False, error=f"{type(e).__name__}: {e}")
        return RequestResult(ok=False, error="unreachable")

    async def _stream_once(self, *, model, prompt, max_tokens, temperature,
                           system, include_usage, force_output=False) -> RequestResult:
        url, payload = self._payload(model, prompt, max_tokens, temperature, system,
                                     include_usage, force_output=force_output)
        start = time.perf_counter()
        first: Optional[float] = None
        chunks = 0
        usage = None
        parts: list[str] = []

        # Ask explicitly for an SSE stream — some gateways only stream token-by-token
        # when the client advertises it (otherwise they buffer the whole response,
        # which makes TTFT == total latency and TPOT unmeasurable).
        headers = {**self._headers(), "Accept": "text/event-stream"}
        async with self._http() as c:
            async with c.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    await resp.aread()          # so .text is available to the caller
                    resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").lower()
                if "event-stream" not in ctype:
                    # Server ignored stream=True and returned a single JSON body.
                    raw = await resp.aread()
                    try:
                        obj = json.loads(raw)
                    except Exception:
                        obj = {}
                    usage = obj.get("usage") or None
                    for ch in (obj.get("choices") or []):
                        piece = (ch.get("message") or {}).get("content") or ch.get("text") or ""
                        if piece:
                            parts.append(piece)
                    # leave chunks=0 / first=None → handled as a non-streamed result below
                else:
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("usage"):
                            usage = obj["usage"]
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        ch = choices[0]
                        if "delta" in ch:
                            piece = (ch.get("delta") or {}).get("content") or ""
                        else:
                            piece = ch.get("text") or ""
                        if piece:
                            if first is None:
                                first = time.perf_counter()
                            parts.append(piece)
                            chunks += 1

        end = time.perf_counter()
        streamed = first is not None
        if first is None:
            first = end
        text = "".join(parts)
        if usage:
            ctoks = int(usage.get("completion_tokens") or chunks or approx_tokens(text))
            ptoks = int(usage.get("prompt_tokens") or approx_tokens(prompt))
        else:
            # No usage block: streamed chunks ≈ tokens; otherwise estimate from text.
            ctoks = chunks if chunks else approx_tokens(text)
            ptoks = approx_tokens(prompt)
        # Decode time = first→end when streamed; for a non-streamed body use total time.
        gen_time = max((end - first) if streamed else (end - start), 1e-9)
        output_tps = ctoks / gen_time if ctoks else 0.0
        return RequestResult(
            ok=True,
            ttft=first - start,
            total_time=end - start,
            prompt_tokens=ptoks,
            completion_tokens=ctoks,
            output_tps=output_tps,
            text=text,
        )
