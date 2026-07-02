"""Fingerprint an HTTP endpoint to tell which LLM server it is."""
from __future__ import annotations

import httpx

from .models import ServerInfo
from .util import Target, candidate_targets


async def _get(client: httpx.AsyncClient, url: str):
    try:
        return await client.get(url)
    except Exception:
        return None


async def detect(host: str, port: int, timeout: float = 4.0, *,
                 scheme: str = "http", base_path: str = "") -> ServerInfo:
    """Probe a single host:port and return its best-guess server type + models."""
    return await detect_target(Target(scheme, host, port, base_path), timeout=timeout)


async def smart_detect(raw_host: str, raw_port=None, timeout: float = 5.0) -> ServerInfo:
    """Auto-resolve a Host field (URL / host:port / bare name) and detect it.

    Tries the derived candidates in order (e.g. https then http) and returns the
    first reachable one; otherwise returns the last attempt with a note listing
    what was tried.
    """
    targets = candidate_targets(raw_host, raw_port)
    last: ServerInfo | None = None
    for t in targets:
        info = await detect_target(t, timeout=timeout)
        if info.reachable:
            return info
        last = info
    if last is not None:
        last.note = "tried: " + ", ".join(t.base_url for t in targets)
        return last
    return ServerInfo(host=str(raw_host or ""), port=0)


async def detect_target(target: Target, timeout: float = 4.0) -> ServerInfo:
    """Probe a resolved target and return its best-guess server type + models."""
    info = ServerInfo(host=target.host, port=target.port,
                      scheme=target.scheme, base_path=target.base_path)
    base = target.base_url
    headers = {"Authorization": "Bearer EMPTY"}

    # verify=False: local LLM servers commonly use self-signed TLS certs.
    async with httpx.AsyncClient(timeout=timeout, headers=headers, verify=False) as c:
        # 1) OpenAI-compatible model list — the common denominator.
        server_hdr = ""
        r = await _get(c, f"{base}/v1/models")
        if r is not None:
            info.reachable = True
            server_hdr = (r.headers.get("server") or "").lower()
            if r.status_code == 200:
                try:
                    data = r.json()
                    info.models = [m.get("id", "?") for m in data.get("data", [])]
                    info.openai_compatible = True
                except Exception:
                    pass

        # 2) Ollama — distinctive native API.
        r = await _get(c, f"{base}/api/tags")
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                info.reachable = True
                info.server_type = "ollama"
                names = [m.get("name", "?") for m in data.get("models", [])]
                if names:
                    info.models = names
                rv = await _get(c, f"{base}/api/version")
                if rv is not None and rv.status_code == 200:
                    info.version = rv.json().get("version")
                return info
            except Exception:
                pass

        # 3) llama.cpp server — /props is unique to it.
        r = await _get(c, f"{base}/props")
        if r is not None and r.status_code == 200:
            try:
                j = r.json()
                if "default_generation_settings" in j or "system_prompt" in j:
                    info.reachable = True
                    info.server_type = "llamacpp"
                    return info
            except Exception:
                pass

        # 4) SGLang — /get_model_info.
        r = await _get(c, f"{base}/get_model_info")
        if r is not None and r.status_code == 200:
            try:
                j = r.json()
                if "model_path" in j:
                    info.reachable = True
                    info.server_type = "sglang"
                    if not info.models and j.get("model_path"):
                        info.models = [j["model_path"]]
                    return info
            except Exception:
                pass

        # 5) TGI (text-generation-inference) — /info with model_id.
        r = await _get(c, f"{base}/info")
        if r is not None and r.status_code == 200:
            try:
                j = r.json()
                mid = j.get("model_id")
                if mid:
                    info.reachable = True
                    info.server_type = "tgi"
                    info.models = [str(mid)]
                    info.version = j.get("version")
                    return info
            except Exception:
                pass

        # 6) vLLM exposes a /version endpoint returning {"version": "..."}.
        if info.openai_compatible:
            r = await _get(c, f"{base}/version")
            if r is not None and r.status_code == 200:
                try:
                    j = r.json()
                    if "version" in j:
                        info.server_type = "vllm"
                        info.version = j["version"]
                        return info
                except Exception:
                    pass

        # 7) Fall back to port/header heuristics for generic OpenAI servers.
        if info.openai_compatible:
            if "uvicorn" in server_hdr and target.port == 8000:
                info.server_type = "vllm"
            elif target.port == 1234:
                info.server_type = "lmstudio"
            elif target.port == 30000:
                info.server_type = "sglang"
            elif target.port == 11434:
                info.server_type = "ollama"
            else:
                info.server_type = "openai-compatible"
        return info
