"""Async client for OpenAI-compatible LLM servers, with timing."""
from __future__ import annotations

import json
import time
from typing import Optional

import httpx

from .models import RequestResult
from .util import Target, approx_tokens


def _accumulate_tool_calls(raw_calls, acc: dict) -> None:
    """Merge one message's or one delta's `tool_calls` entries into `acc` (keyed by
    index — streaming splits a single call's `arguments` across many chunks)."""
    for i, tc in enumerate(raw_calls or []):
        idx = tc.get("index", i)
        slot = acc.setdefault(idx, {"name": "", "args": ""})
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["name"] += fn["name"]
        if fn.get("arguments"):
            slot["args"] += fn["arguments"]


def _finalize_tool_calls(acc: dict) -> list:
    """acc (index -> {name, args-as-json-text}) → [(name, parsed-or-raw-args), …]."""
    out = []
    for slot in acc.values():
        if not slot["name"]:
            continue
        try:
            args = json.loads(slot["args"]) if slot["args"] else {}
        except Exception:
            args = slot["args"]
        out.append((slot["name"], args))
    return out


def _collect_logprobs(lp, out: list) -> None:
    """Pull per-token logprobs out of a chunk's `logprobs` object, in both the
    chat form ({"content": [{"logprob": ...}]}) and the completions form
    ({"token_logprobs": [...]}). No-op when the server sends none."""
    if not isinstance(lp, dict):
        return
    for it in (lp.get("content") or []):
        v = it.get("logprob") if isinstance(it, dict) else None
        if isinstance(v, (int, float)):
            out.append(float(v))
    for v in (lp.get("token_logprobs") or []):
        if isinstance(v, (int, float)):
            out.append(float(v))


class LLMClient:
    """Talks to an OpenAI-compatible /v1 endpoint and measures speed.

    Works with vLLM, SGLang, llama.cpp server, Ollama (/v1), TGI's
    OpenAI route, LM Studio, LocalAI, etc.
    """

    def __init__(self, host: str, port: int, *, api_key: str = "EMPTY",
                 scheme: str = "http", timeout: float = 120.0, endpoint: str = "chat",
                 base_path: str = "", extra_body: Optional[dict] = None):
        self.host = host
        self.port = port
        self.scheme = scheme
        self.base_path = base_path
        self.base_url = Target(scheme, host, port, base_path).base_url
        self.api_key = api_key or "EMPTY"
        self.timeout = timeout
        self.endpoint = endpoint  # "chat" or "completions"
        # Extra top-level request-body fields merged into every request — e.g.
        # {"chat_template_kwargs": {"enable_thinking": False}} to test a Qwen3-style
        # model in its non-thinking (agentic) mode. Servers ignore unknown fields.
        self.extra_body = dict(extra_body) if extra_body else {}

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

    async def list_models_raw(self) -> list:
        """Return the raw /v1/models entries (dicts), for inspecting the metadata
        routers require (pricing, context_length). [] on any failure."""
        try:
            async with self._http() as c:
                r = await c.get(f"{self.base_url}/v1/models", headers=self._headers())
                r.raise_for_status()
                return r.json().get("data", []) or []
        except Exception:
            return []

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
                 force_output=False, stop=None, top_p=None, seed=None, logprobs=False,
                 tools=None, tool_choice=None):
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
        # Optional sampling / stopping controls — used by the provider-readiness
        # contract probes to verify the server honours them. Omitted when unset so
        # the throughput paths send exactly the same body they always have.
        if stop:
            payload["stop"] = stop
        if top_p is not None:
            payload["top_p"] = top_p
        if seed is not None:
            payload["seed"] = seed
        if logprobs:
            # Ask for per-token logprobs to fingerprint the model's confidence.
            # Chat and completions spell this differently.
            if self.endpoint == "completions":
                payload["logprobs"] = 1
            else:
                payload["logprobs"] = True
                payload["top_logprobs"] = 1
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
        if tools and self.endpoint != "completions":
            # Native OpenAI function-calling — the API surface a router actually
            # sends (as opposed to a prompt-embedded convention like Hermes' XML).
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        # Instance-level extra body last, so a per-run setting (e.g. disable
        # thinking) applies to every request without threading a param everywhere.
        for k, v in self.extra_body.items():
            payload[k] = v
        return url, payload

    async def generate(self, *, model: str, prompt: str, max_tokens: int = 128,
                       temperature: float = 0.0, system: Optional[str] = None,
                       force_output: bool = False, stop=None, top_p: Optional[float] = None,
                       seed: Optional[int] = None, logprobs: bool = False,
                       tools=None, tool_choice=None) -> RequestResult:
        """Stream one completion and return timing/throughput metrics.

        `force_output` requests exactly `max_tokens` of output (ignore_eos /
        min_tokens) so throughput is measured on a fixed decode length.
        `stop` / `top_p` / `seed` are passed straight through when set (used by
        the provider-readiness contract checks). `tools` (OpenAI function-calling
        schema list) / `tool_choice` are forwarded as-is; any tool calls the model
        makes come back in `RequestResult.tool_calls`.
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
                    stop=stop, top_p=top_p, seed=seed, logprobs=logprobs,
                    tools=tools, tool_choice=tool_choice,
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

    async def stream_abort(self, *, model: str, prompt: str, max_tokens: int = 512) -> bool:
        """Open a streaming completion, read until the first token, then disconnect
        (close the connection) — simulating a client cancel mid-generation. Returns
        True if a token arrived before we aborted. Used to test whether the server
        frees the slot on disconnect (a router cancels aborted user requests)."""
        url, payload = self._payload(model, prompt, max_tokens, 0.0, None, False)
        headers = {**self._headers(), "Accept": "text/event-stream"}
        try:
            async with self._http() as c:
                async with c.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        return False
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        ch = (obj.get("choices") or [{}])[0]
                        piece = (ch.get("delta") or {}).get("content") or ch.get("text") or ""
                        if piece:
                            # First token in hand — break out; leaving the `async with`
                            # closes the connection, which is the client disconnect.
                            return True
            return False
        except Exception:
            return False

    async def _stream_once(self, *, model, prompt, max_tokens, temperature,
                           system, include_usage, force_output=False,
                           stop=None, top_p=None, seed=None, logprobs=False,
                           tools=None, tool_choice=None) -> RequestResult:
        url, payload = self._payload(model, prompt, max_tokens, temperature, system,
                                     include_usage, force_output=force_output,
                                     stop=stop, top_p=top_p, seed=seed, logprobs=logprobs,
                                     tools=tools, tool_choice=tool_choice)
        start = time.perf_counter()
        first: Optional[float] = None
        chunks = 0
        usage = None
        finish_reason = ""
        logprob_vals: list[float] = []
        parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict = {}  # index -> {"name": str, "args": str} — streamed tool_calls

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
                        msg = ch.get("message") or {}
                        piece = msg.get("content") or ch.get("text") or ""
                        rpiece = msg.get("reasoning_content") or msg.get("reasoning") or ""
                        if ch.get("finish_reason"):
                            finish_reason = ch["finish_reason"]
                        _collect_logprobs(ch.get("logprobs"), logprob_vals)
                        _accumulate_tool_calls(msg.get("tool_calls"), tool_acc)
                        if piece:
                            parts.append(piece)
                        if rpiece:
                            reasoning_parts.append(rpiece)
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
                        if ch.get("finish_reason"):
                            finish_reason = ch["finish_reason"]
                        _collect_logprobs(ch.get("logprobs"), logprob_vals)
                        tc_delta = None
                        if "delta" in ch:
                            delta = ch.get("delta") or {}
                            piece = delta.get("content") or ""
                            rpiece = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            tc_delta = delta.get("tool_calls")
                        else:
                            piece = ch.get("text") or ""
                            rpiece = ""
                        if tc_delta:
                            _accumulate_tool_calls(tc_delta, tool_acc)
                        # A reasoning model may stream only reasoning tokens (empty
                        # content), and a tool-calling response may carry no content
                        # at all — count either as activity, and set TTFT on the
                        # first token of ANY kind, or streaming looks un-streamed.
                        if piece or rpiece or tc_delta:
                            if first is None:
                                first = time.perf_counter()
                            chunks += 1
                            if piece:
                                parts.append(piece)
                            if rpiece:
                                reasoning_parts.append(rpiece)

        end = time.perf_counter()
        streamed = first is not None
        if first is None:
            first = end
        text = "".join(parts)
        # Track whether either count is a guess rather than a server-reported usage
        # number — if so, every tok/s derived from it is only approximate, and the
        # caller should say so (a silently-dropped usage block reads as "success").
        c_usage = bool(usage and usage.get("completion_tokens"))
        p_usage = bool(usage and usage.get("prompt_tokens"))
        if usage:
            ctoks = int(usage.get("completion_tokens") or chunks or approx_tokens(text))
            ptoks = int(usage.get("prompt_tokens") or approx_tokens(prompt))
        else:
            # No usage block: streamed chunks ≈ tokens; otherwise estimate from text.
            ctoks = chunks if chunks else approx_tokens(text)
            ptoks = approx_tokens(prompt)
        est_tokens = not (c_usage and p_usage)
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
            est_tokens=est_tokens,
            finish_reason=finish_reason,
            stream_chunks=chunks,
            logprob_avg=(sum(logprob_vals) / len(logprob_vals)) if logprob_vals else None,
            reasoning="".join(reasoning_parts),
            tool_calls=_finalize_tool_calls(tool_acc),
        )
