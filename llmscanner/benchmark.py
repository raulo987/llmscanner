"""Benchmark routines: latency, throughput, load, context/prefill, sanity."""
from __future__ import annotations

import asyncio
import json
import math
import random
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .client import LLMClient
from .models import RequestResult
from .util import approx_tokens

# A prompt that strongly encourages the model to keep generating, so that
# throughput numbers reflect real decode speed (not an early EOS).
LONG_PROMPT = (
    "Write an extremely long and detailed technical essay about the history, "
    "architecture and internals of modern operating systems: schedulers, virtual "
    "memory, file systems, and networking. Be verbose and keep writing, do not stop."
)

SANITY_PROMPT = "What is 17 + 25? Give the final answer as a number."

# Capability probes (compliance / integrity / model-fit / recall) retry a transient
# 5xx or connection error a couple of times, so a momentary server hiccup (e.g. a
# 503 overload) doesn't fail the whole test. The load/soak paths deliberately do
# NOT retry — there a 503 is the admission-control signal being measured.
_PROBE_RETRIES = 2

_VOCAB = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing",
          "elit", "sed", "tempor", "incididunt", "labore", "magna", "aliqua",
          "enim", "minim", "veniam", "quis", "nostrud", "exercitation"]


def _filler(approx_tokens: int, lead=None) -> str:
    """Build filler text of roughly `approx_tokens` tokens (~0.9 words/token)."""
    n = max(1, int(approx_tokens * 0.9))
    words = [random.choice(_VOCAB) for _ in range(n)]
    pre = f"{lead} " if lead is not None else ""
    return pre + " ".join(words)


_SALT_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def _unique_prefix(n_tokens: int = 64) -> str:
    """A high-entropy preamble that is different on every call.

    Prefix-affinity routers (e.g. ApiRouter) send requests that share a prompt
    prefix to the *same* backend for KV-cache reuse. Under a synthetic load test
    that would pin all our "parallel users" onto one GPU while the others sit
    idle — badly under-measuring real concurrency. Prepending a distinct random
    preamble, long enough to span the router's prefix blocks, makes each request
    read as its own conversation so the load actually spreads across backends.
    """
    return " ".join("".join(random.choice(_SALT_CHARS) for _ in range(6))
                    for _ in range(max(1, n_tokens)))


@dataclass
class LoadStats:
    requests: int
    success: int
    wall_time: float
    total_completion_tokens: int
    aggregate_tps: float
    ttft_p50: float
    ttft_p95: float
    latency_p50: float
    latency_p95: float
    per_request_tps_mean: float
    ttft_p99: float = 0.0
    latency_p99: float = 0.0
    total_prompt_tokens: int = 0
    input_tps: float = 0.0          # prompt (input) tokens / wall — prefill/ingest rate
    total_tps: float = 0.0          # (prompt + completion) tokens / wall — total work rate
    tpot_ms: float = 0.0            # mean time per output token, excl. 1st (decode latency)
    req_per_s: float = 0.0          # request throughput — successful requests / wall
    peak_out_tps: float = 0.0       # peak output tok/s over any 1 s window (completion-based)
    est_frac: float = 0.0           # fraction of ok requests whose token counts were estimated
    errors: list[str] = field(default_factory=list)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


async def latency(client: LLMClient, model: str, *, max_tokens: int = 128,
                  prompt: str = LONG_PROMPT) -> RequestResult:
    """Single request — measures TTFT, total time, decode tok/s."""
    return await client.generate(model=model, prompt=prompt, max_tokens=max_tokens)


async def throughput(client: LLMClient, model: str, *, max_tokens: int = 256,
                     runs: int = 3, prompt: str = LONG_PROMPT):
    """Run N sequential requests; return (all_results, ok_results)."""
    results: list[RequestResult] = []
    for i in range(runs):
        r = await client.generate(model=model, prompt=f"[{i}] {prompt}", max_tokens=max_tokens)
        results.append(r)
    ok = [r for r in results if r.ok]
    return results, ok


async def load(client: LLMClient, model: str, *, concurrency: int = 8,
               requests: int = 32, max_tokens: int = 128, prompt: str = LONG_PROMPT,
               ctx_tokens: Optional[int] = None, distinct_prefix: bool = False,
               force_output: bool = True,
               progress_cb: Optional[Callable[[int, int], None]] = None) -> LoadStats:
    """Fire `requests` requests, `concurrency` at a time; aggregate stats.

    When `ctx_tokens` is given, each request gets its own fresh ~ctx_tokens
    filler prompt (unique per request, so prefix caching can't skew the numbers)
    — used by the optimum finder to load-test at a controlled prompt size.

    When `distinct_prefix` is set, every request starts with a unique
    high-entropy preamble so prefix-affinity routers spread the load across
    backends instead of pinning it all to one GPU (see `_unique_prefix`).

    `force_output` (default on) makes every request decode exactly `max_tokens`
    tokens, so throughput is measured on a fixed output length and is comparable
    across requests — without it a model may stop after ~1 token.
    """
    sem = asyncio.Semaphore(concurrency)
    done = 0
    salt_tokens = 64  # length of the per-request unique preamble
    comp_events: list[tuple[float, RequestResult]] = []  # (completion perf_counter, result)

    async def one(i: int) -> RequestResult:
        nonlocal done
        async with sem:
            salt = (_unique_prefix(salt_tokens) + "\n") if distinct_prefix else ""
            if ctx_tokens:
                # Ask for a long continuation so the model actually decodes up to
                # `max_tokens` (a "reply ok" prompt would stop after ~1 token and
                # make decode throughput meaningless). Reserve the salt from the
                # token budget so the total prompt still lands near ctx_tokens.
                budget = max(1, ctx_tokens - (salt_tokens if distinct_prefix else 0))
                body = (salt + _filler(budget, lead=random.randint(0, 10 ** 9)) +
                        "\n\nUsing the text above as context, write a long, detailed "
                        "continuation. Keep writing and do not stop.")
            else:
                # Nonce avoids prefix-cache hits skewing the numbers.
                body = salt + f"[req {i}] {prompt}"
            r = await client.generate(model=model, prompt=body, max_tokens=max_tokens,
                                      force_output=force_output)
        comp_events.append((time.perf_counter(), r))
        done += 1
        if progress_cb:
            progress_cb(done, requests)
        return r

    start = time.perf_counter()
    results = await asyncio.gather(*(one(i) for i in range(requests)))
    wall = time.perf_counter() - start

    ok = [r for r in results if r.ok]
    errors = [r.error for r in results if not r.ok]
    total_ctoks = sum(r.completion_tokens for r in ok)
    total_ptoks = sum(r.prompt_tokens for r in ok)
    ttfts = [r.ttft for r in ok]
    lats = [r.total_time for r in ok]
    tpss = [r.output_tps for r in ok]
    # TPOT — mean time per output token excluding the first (decode latency). Uses
    # ttft/total_time, so it's meaningful only when the server streams (ttft < total).
    tpots = [(r.total_time - r.ttft) / (r.completion_tokens - 1)
             for r in ok if r.completion_tokens > 1 and r.total_time > r.ttft]
    # Peak output tok/s — most completion tokens delivered in any 1 s window.
    ev = sorted((t - start, r.completion_tokens) for t, r in comp_events if r.ok)
    peak = 0.0
    for i in range(len(ev)):
        t0 = ev[i][0]
        peak = max(peak, float(sum(ct for t, ct in ev if t0 <= t < t0 + 1.0)))
    return LoadStats(
        requests=requests,
        success=len(ok),
        wall_time=wall,
        total_completion_tokens=total_ctoks,
        aggregate_tps=total_ctoks / wall if wall else 0.0,
        ttft_p50=_pct(ttfts, 0.5),
        ttft_p95=_pct(ttfts, 0.95),
        ttft_p99=_pct(ttfts, 0.99),
        latency_p50=_pct(lats, 0.5),
        latency_p95=_pct(lats, 0.95),
        latency_p99=_pct(lats, 0.99),
        per_request_tps_mean=statistics.mean(tpss) if tpss else 0.0,
        total_prompt_tokens=total_ptoks,
        # Input, output and total token throughput are all aggregate rates over the
        # same batch wall time, so input_tps + aggregate_tps == total_tps. Output
        # alone (aggregate_tps) looks tiny when prompts are large and generations
        # short, because it ignores the input tokens the server had to prefill.
        input_tps=total_ptoks / wall if wall else 0.0,
        total_tps=(total_ptoks + total_ctoks) / wall if wall else 0.0,
        tpot_ms=1000.0 * statistics.mean(tpots) if tpots else 0.0,
        req_per_s=len(ok) / wall if wall else 0.0,
        peak_out_tps=peak,
        est_frac=(sum(1 for r in ok if r.est_tokens) / len(ok)) if ok else 0.0,
        errors=errors[:5],
    )


async def context_test(client: LLMClient, model: str, *, ctx_tokens: int = 2048,
                       max_tokens: int = 16):
    """Send a ~ctx_tokens prompt and measure prefill (prompt) speed.

    Returns (RequestResult, prefill_tokens_per_sec). Also a good check that
    the server actually accepts the requested context length.
    """
    vocab = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing",
             "elit", "sed", "tempor", "incididunt", "labore", "magna", "aliqua",
             "enim", "minim", "veniam", "quis", "nostrud", "exercitation"]
    n_words = max(1, int(ctx_tokens * 0.9))
    words = [random.choice(vocab) for _ in range(n_words)]
    nonce = random.randint(0, 10_000_000)
    prompt = f"{nonce} " + " ".join(words) + "\nReply with the single word: done."
    r = await client.generate(model=model, prompt=prompt, max_tokens=max_tokens)
    prefill_tps = (r.prompt_tokens / r.ttft) if (r.ok and r.ttft > 0) else 0.0
    return r, prefill_tps


async def sanity(client: LLMClient, model: str):
    """Quick correctness check (not just speed). Returns (result, passed, expected, got).

    Uses a generous token budget and accepts several spellings of the answer so
    that reasoning/instruct models aren't failed on formatting.
    """
    r = await client.generate(model=model, prompt=SANITY_PROMPT, max_tokens=64, temperature=0.0)
    got = (r.text or "").strip()
    low = got.lower()
    passed = bool(r.ok and ("42" in got or "forty-two" in low or "forty two" in low))
    return r, passed, "42", got


# --------------------------------------------------------------------------- #
#  Advanced tests
# --------------------------------------------------------------------------- #

@dataclass
class SweepPoint:
    concurrency: int
    requests: int
    success: int
    agg_tps: float
    ttft_p50: float
    ttft_p95: float
    lat_p50: float
    lat_p95: float


async def concurrency_sweep(client: LLMClient, model: str, *, levels: list[int],
                            max_tokens: int = 128, req_per_worker: int = 3,
                            progress_cb: Optional[Callable[[int, int], None]] = None
                            ) -> list[SweepPoint]:
    """Run the load test at increasing concurrency to find the saturation point."""
    points: list[SweepPoint] = []
    last = levels[-1] if levels else 0
    for c in levels:
        reqs = max(c * req_per_worker, 4)
        stt = await load(client, model, concurrency=c, requests=reqs, max_tokens=max_tokens)
        points.append(SweepPoint(c, reqs, stt.success, stt.aggregate_tps,
                                 stt.ttft_p50, stt.ttft_p95,
                                 stt.latency_p50, stt.latency_p95))
        if progress_cb:
            progress_cb(c, last)
    return points


async def prefix_cache_test(client: LLMClient, model: str, *, prefix_tokens: int = 2048):
    """Send the same long prefix twice; a much faster 2nd TTFT ⇒ prefix caching."""
    prefix = _filler(prefix_tokens, lead=random.randint(0, 10 ** 9))
    q = "\n\nBased on the text above, reply with the single word: ok."
    r_cold = await client.generate(model=model, prompt=prefix + q, max_tokens=4, temperature=0.0)
    r_warm = await client.generate(model=model, prompt=prefix + q, max_tokens=4, temperature=0.0)
    cold, warm = r_cold.ttft, r_warm.ttft
    ok = r_cold.ok and r_warm.ok
    speedup = (cold / warm) if (ok and warm > 0) else 0.0
    return {
        "ok": ok,
        "cold_ttft": cold,
        "warm_ttft": warm,
        "speedup": speedup,
        "likely": bool(ok and speedup >= 1.5),
        "prompt_tokens": r_warm.prompt_tokens,
        "error": r_cold.error or r_warm.error,
    }


DET_PROMPT = "List the first 20 prime numbers as a comma-separated line, then stop."


async def determinism_test(client: LLMClient, model: str, *, runs: int = 5,
                           max_tokens: int = 128, prompt: str = DET_PROMPT):
    """Same prompt at temperature 0, N times — are the outputs identical?"""
    outs = []
    for _ in range(max(2, runs)):
        r = await client.generate(model=model, prompt=prompt, max_tokens=max_tokens, temperature=0.0)
        outs.append(r.text.strip() if r.ok else None)
    valid = [o for o in outs if o is not None]
    if not valid:
        return {"runs": runs, "valid": 0, "unique": 0, "identical": 0,
                "pct": 0.0, "deterministic": False, "sample": ""}
    first = valid[0]
    identical = sum(1 for o in valid if o == first)
    return {
        "runs": len(outs),
        "valid": len(valid),
        "unique": len(set(valid)),
        "identical": identical,
        "pct": 100.0 * identical / len(valid),
        "deterministic": len(set(valid)) == 1,
        "sample": first[:80],
    }


def _parse_ctx_limit(msg: str):
    """Extract the max context length from a server error message, if present."""
    if not msg:
        return None
    for pat in (r"maximum context length is (\d+)",
                r"context length of (\d+)",
                r"max(?:imum)?[ _]?(?:model[ _]?)?len(?:gth)?(?:\s+is|\s+of)?\s*(\d+)"):
        m = re.search(pat, msg)
        if m:
            return int(m.group(1))
    return None


async def context_limit_probe(client: LLMClient, model: str, *, low: int = 256,
                              high: int = 16384):
    """Find the model's max context length with as few oversized requests as
    possible: prefer the server-advertised value, then parse one over-limit
    error, and only fall back to a binary search if neither is available.
    """
    # 1) Advertised by the server (vLLM exposes max_model_len) — no oversized requests.
    advertised = await client.model_max_len(model)
    if advertised:
        return {"tokens": advertised, "approx": advertised, "source": "advertised",
                "low": low, "high": high, "error": ""}

    async def accepts(n):
        prompt = _filler(n, lead=random.randint(0, 10 ** 9))
        r = await client.generate(model=model, prompt=prompt, max_tokens=1, temperature=0.0)
        return r.ok, r.prompt_tokens, r.error

    # 2) A single probe at `high`: if it errors, read the real limit from the message.
    ok_hi, ptoks_hi, err_hi = await accepts(high)
    if ok_hi:
        return {"tokens": ptoks_hi, "approx": high, "source": "≥ tested",
                "low": low, "high": high, "error": ""}
    parsed = _parse_ctx_limit(err_hi or "")
    if parsed:
        return {"tokens": parsed, "approx": parsed, "source": "from error",
                "low": low, "high": high, "error": ""}

    # 3) Fallback: binary search (only when the error wasn't machine-readable).
    ok_low, ptoks_low, err = await accepts(low)
    if not ok_low:
        return {"tokens": 0, "approx": 0, "source": "failed",
                "low": low, "high": high, "error": err}
    lo, hi, best, best_tok = low, high, low, ptoks_low
    while lo < hi:
        mid = (lo + hi + 1) // 2
        ok, ptoks, _e = await accepts(mid)
        if ok:
            best, best_tok, lo = mid, ptoks, mid
        else:
            hi = mid - 1
    return {"tokens": best_tok, "approx": best, "source": "probed",
            "low": low, "high": high, "error": ""}


async def needle_test(client: LLMClient, model: str, *, ctx_tokens: int = 4096,
                      depths=(0.1, 0.5, 0.9), answer_tokens: int = 32):
    """Hide a secret code in a long context at several depths and ask for it back.

    `answer_tokens` bounds the reply; give a reasoning model room (it thinks before
    answering). The code is matched against the visible answer (<think> stripped)."""
    results = []
    total = max(1, int(ctx_tokens * 0.9))
    for d in depths:
        code = f"{random.randint(1000, 9999)}-{random.choice(['BLUE', 'RED', 'GOLD', 'JADE'])}-{random.randint(10, 99)}"
        needle = f"  The secret access code is {code}.  "
        before = int(total * d)
        prompt = (_filler(before) + needle + _filler(max(1, total - before)) +
                  "\n\nQuestion: What is the secret access code? Answer with the code only.")
        r = await client.generate(model=model, prompt=prompt, max_tokens=answer_tokens,
                                  temperature=0.0, retries=_PROBE_RETRIES)
        got = _visible_answer(r)
        results.append({"depth": d, "passed": bool(r.ok and code in got),
                        "code": code, "got": got[:40], "error": r.error})
    return {"results": results, "passed": sum(1 for x in results if x["passed"]),
            "total": len(results)}


# --------------------------------------------------------------------------- #
#  Optimum finder — auto-tune concurrency and request size
# --------------------------------------------------------------------------- #

@dataclass
class OptPoint:
    """One measured (concurrency, request-size, gen-length) operating point."""
    phase: str          # "concurrency" | "size" | "gen" | "profile"
    concurrency: int
    ctx_tokens: int
    gen_tokens: int     # max output tokens requested for this measurement
    requests: int
    success: int
    agg_tps: float          # OUT tok/s — completion tokens / wall (decode/output rate)
    input_tps: float        # IN tok/s — prompt tokens / wall (prefill/ingest rate)
    total_tps: float        # TOTAL tok/s — (prompt + completion) / wall
    lat_p50: float
    lat_p95: float
    ttft_p95: float
    tpot_ms: float          # mean time per output token, excl. 1st (decode latency)
    req_per_s: float        # request throughput (successful requests / wall)
    peak_out_tps: float     # peak output tok/s over any 1 s window
    gen_actual: float       # mean output tokens actually generated per request
    est_frac: float         # fraction of requests whose token counts were estimated
    feasible: bool
    note: str = ""


def _frontier_sizes(max_ctx: int, n: int = 3, lo: int = 1024) -> list[int]:
    """Geometrically-spaced context sizes from `lo` up to ~90% of max_ctx.

    Stays just under the hard limit so the frontier measures *capacity* under
    load, not the model rejecting an over-limit prompt.
    """
    hi = max(lo, int(max_ctx * 0.9))
    if hi <= lo or n <= 1:
        return [hi]
    ratio = (hi / lo) ** (1.0 / (n - 1))
    return sorted({int(lo * ratio ** i) for i in range(n)})


async def _measure(client: LLMClient, model: str, *, phase: str, concurrency: int,
                   ctx_tokens: int, max_tokens: int, requests: int,
                   min_success: float, distinct_prefix: bool = True,
                   settle_s: float = 0.0) -> OptPoint:
    # Let the server drain the previous batch (free KV cache, empty the queue,
    # let any rate-limit window reset) so this measurement isn't contaminated by
    # leftover load from a different concurrency/size.
    if settle_s > 0:
        await asyncio.sleep(settle_s)
    stt = await load(client, model, concurrency=concurrency, requests=requests,
                     max_tokens=max_tokens, ctx_tokens=ctx_tokens,
                     distinct_prefix=distinct_prefix)
    # "at most (1 - min_success) of requests may fail". Floor the allowed-failure
    # count so the rule is monotonic in `requests` (a larger pool is never harder
    # to pass than a smaller one at the same min_success).
    allowed_fail = int(requests * (1.0 - min_success))
    feasible = stt.success > 0 and (requests - stt.success) <= allowed_fail
    gen_actual = stt.total_completion_tokens / stt.success if stt.success else 0.0
    note = ""
    if not feasible and stt.errors:
        note = stt.errors[0][:80]
    elif feasible and max_tokens >= 8 and gen_actual < 0.5 * max_tokens:
        # The server generated far fewer tokens than requested — ignore_eos was
        # not honored (it likely fell back), so out/TPOT here understate decode.
        note = f"under-gen: {gen_actual:.0f}/{max_tokens} out tok (ignore_eos not honored?)"
    return OptPoint(phase, concurrency, ctx_tokens, max_tokens, requests, stt.success,
                    stt.aggregate_tps, stt.input_tps, stt.total_tps, stt.latency_p50,
                    stt.latency_p95, stt.ttft_p95, stt.tpot_ms, stt.req_per_s,
                    stt.peak_out_tps, gen_actual, stt.est_frac, feasible, note)


DEFAULT_OPT_SIZES = [1024, 2048, 4096, 8192, 16384]


async def find_optima(client: LLMClient, model: Optional[str], *,
                      conc_levels: list[int], base_ctx: int = 1024,
                      gen_tokens: int = 64, req_per_worker: int = 4,
                      min_success: float = 0.9, plateau_frac: float = 0.05,
                      plateau_patience: int = 2, knee_frac: float = 0.9,
                      ctx_cap: int = 262144, do_frontier: bool = True,
                      sizes: Optional[list[int]] = None, frontier_points: int = 3,
                      distinct_prefix: bool = True, do_gen_sweep: bool = False,
                      gen_sizes: Optional[list[int]] = None, do_profiles: bool = False,
                      profiles: Optional[list] = None, profile_conc: int = 16,
                      settle_s: float = 2.0,
                      on_status: Optional[Callable[[str], None]] = None,
                      on_point: Optional[Callable[[OptPoint], None]] = None) -> dict:
    """Auto-tune a server: find the best concurrency and the largest workable
    request size, plus how the two trade off (the KV-cache frontier).

    Phases:
      A. Max context — the largest single-request prompt that succeeds (uses the
         server-advertised limit when available, else probes).
      B. Concurrency sweep at a modest context — climbs `conc_levels`, stopping
         early once throughput plateaus or a level starts failing. Reports the
         peak-throughput concurrency and the efficiency "knee" (lowest
         concurrency reaching `knee_frac` of peak).
      C. Size sweep (optional) — for each request size in `sizes` (default
         1024/2048/4096/8192/16384, skipping any above the max context), climbs
         concurrency to find both the peak-throughput concurrency and the highest
         concurrency that still succeeds at that size. Shows the size/parallelism
         trade-off (larger prompts ⇒ fewer fit in KV cache at once).

    Streams each measured point via `on_point` and phase notes via `on_status`.
    Runtime is bounded by early-stop plus the client's request timeout.
    """
    say = on_status or (lambda *_: None)
    emit = on_point or (lambda *_: None)
    levels = sorted({c for c in conc_levels if c >= 1})
    summary: dict = {
        "model": model, "points": [], "max_ctx": 0, "max_ctx_source": "",
        "peak": None, "knee": None, "max_feasible_c": None, "frontier": [],
        "sizes": [], "sizes_skipped": [], "gen_sizes": [], "gen_sweep_conc": 0,
        "profiles": [], "profile_conc": profile_conc, "base_ctx": base_ctx, "aborted": "",
    }

    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")
    summary["model"] = model
    say(f"model: {model}")

    # ---- Phase A: max context (single request) ----
    say(f"Phase A · probing max context (cap {ctx_cap:,})…")
    probe = await context_limit_probe(client, model, low=256, high=ctx_cap)
    max_ctx = int(probe["tokens"] or probe["approx"] or 0)
    summary["max_ctx"] = max_ctx
    summary["max_ctx_source"] = probe["source"]
    say(f"max context ≈ {max_ctx:,} tokens ({probe['source']})")

    # ---- Phase B: concurrency sweep at base_ctx ----
    ctx_b = min(base_ctx, max_ctx) if max_ctx else base_ctx
    say(f"Phase B · concurrency sweep @ ctx≈{ctx_b:,}…")
    pts: list[OptPoint] = []
    best_tps = 0.0
    plateau = 0
    for c in levels:
        reqs = max(c * req_per_worker, 4)
        say(f"  concurrency {c} · {reqs} requests…")
        p = await _measure(client, model, phase="concurrency", concurrency=c,
                           ctx_tokens=ctx_b, max_tokens=gen_tokens, requests=reqs,
                           min_success=min_success, distinct_prefix=distinct_prefix,
                           settle_s=settle_s)
        pts.append(p)
        summary["points"].append(p)
        emit(p)
        if not p.feasible:
            summary["aborted"] = f"concurrency {c} failed ({p.success}/{reqs} ok)"
            break
        # Rank by total throughput (prompt+completion/s): it credits prefill work,
        # so it's the honest measure of how much the server gets done per second.
        if p.total_tps > best_tps * (1 + plateau_frac):
            plateau = 0
        else:
            plateau += 1
        best_tps = max(best_tps, p.total_tps)
        if plateau >= plateau_patience:
            say(f"  throughput plateaued at c={c} — stopping climb")
            break

    feasible_pts = [p for p in pts if p.feasible and p.success > 0]
    if feasible_pts:
        peak = max(feasible_pts, key=lambda p: p.total_tps)
        knee = next((p for p in feasible_pts
                     if p.total_tps >= knee_frac * peak.total_tps), peak)
        summary["peak"] = peak
        summary["knee"] = knee
        summary["max_feasible_c"] = max(p.concurrency for p in feasible_pts)

    # ---- Phase C: per-size concurrency sweep (the size × parallelism frontier) ----
    if do_frontier:
        wanted = sizes if sizes is not None else _frontier_sizes(max_ctx, frontier_points, lo=base_ctx)
        wanted = sorted({s for s in wanted if s >= 1})
        usable = [s for s in wanted if not max_ctx or s <= max_ctx]
        skipped = [s for s in wanted if max_ctx and s > max_ctx]
        summary["sizes"] = usable
        summary["sizes_skipped"] = skipped
        if skipped:
            say(f"Phase C · skipping {', '.join(f'{s:,}' for s in skipped)} "
                f"(> max context {max_ctx:,})")
        say("Phase C · per-size concurrency sweep…")
        for cs in usable:
            say(f"  request size {cs:,} tok — climbing concurrency…")
            feas: list[OptPoint] = []
            for c in levels:
                reqs = max(c, 2)  # 1 request per worker keeps big-size probes short
                p = await _measure(client, model, phase="size", concurrency=c,
                                   ctx_tokens=cs, max_tokens=min(gen_tokens, 32),
                                   requests=reqs, min_success=min_success,
                                   distinct_prefix=distinct_prefix, settle_s=settle_s)
                summary["points"].append(p)
                emit(p)
                if p.feasible:
                    feas.append(p)
                else:
                    break  # this size can't sustain more concurrency
            max_c = max((p.concurrency for p in feas), default=0)
            peak_row = max(feas, key=lambda p: p.total_tps) if feas else None
            summary["frontier"].append({
                "ctx": cs, "max_c": max_c,
                "peak_c": peak_row.concurrency if peak_row else 0,
                "peak_tps": peak_row.total_tps if peak_row else 0.0,
                "row": peak_row,
            })

    # ---- Phase D: generation-length sweep (how output length affects rates) ----
    if do_gen_sweep and gen_sizes:
        glist = sorted({g for g in gen_sizes if g >= 1})
        # Run at a single representative concurrency (the knee, else the smallest
        # level) so only the output length varies.
        gc = summary["knee"].concurrency if summary.get("knee") else (min(levels) if levels else 1)
        ctx_d = min(base_ctx, max_ctx) if max_ctx else base_ctx
        summary["gen_sizes"] = glist
        summary["gen_sweep_conc"] = gc
        say(f"Phase D · generation-length sweep @ concurrency {gc}, ctx≈{ctx_d:,}…")
        for g in glist:
            reqs = max(gc * req_per_worker, 4)
            say(f"  gen {g} tok · {reqs} requests…")
            p = await _measure(client, model, phase="gen", concurrency=gc,
                               ctx_tokens=ctx_d, max_tokens=g, requests=reqs,
                               min_success=min_success, distinct_prefix=distinct_prefix,
                           settle_s=settle_s)
            summary["points"].append(p)
            emit(p)

    # ---- Phase E: workload profiles (fixed (input,output) at a set concurrency) ----
    # Mirrors the standard vLLM serving benchmark (prompt-heavy / decode-heavy /
    # balanced) so results are directly comparable to published numbers.
    if do_profiles and profiles:
        pc = max(1, profile_conc)
        reqs = max(pc, 2)
        say(f"Phase E · workload profiles @ concurrency {pc}…")
        for (pin, pout) in profiles:
            pin, pout = int(pin), int(pout)
            if max_ctx and pin > max_ctx:
                say(f"  {pin}/{pout} — skipped (input > max context {max_ctx:,})")
                continue
            say(f"  in {pin:,} / out {pout:,} tok · {reqs} requests…")
            p = await _measure(client, model, phase="profile", concurrency=pc,
                               ctx_tokens=pin, max_tokens=pout, requests=reqs,
                               min_success=min_success, distinct_prefix=distinct_prefix,
                           settle_s=settle_s)
            summary["points"].append(p)
            summary["profiles"].append({"in": pin, "out": pout, "conc": pc, "row": p})
            emit(p)

    return summary


# --------------------------------------------------------------------------- #
#  Soak test — sustained load over a fixed duration → tokens/hour
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
#  TheEye — a real production workload mix (per-task call rate + in/out sizes)
# --------------------------------------------------------------------------- #

# (task, calls_per_30min, (in mean, in p95, in max), (out mean, out p95, out max))
THEEYE_TASKS = [
    ("classification",          108842, (1159, 3323, 11906), (117, 145, 2048)),
    ("social_image_understand", 103973, (1266, 3401, 16495), (135, 215, 517)),
    ("extraction",               77051, (3608, 6609, 12633), (809, 1954, 8192)),
    ("causal_relevance",         42063, (265, 290, 366),      (42, 60, 512)),
    ("signal_relevance_batch",   42047, (2245, 2744, 3432),   (239, 789, 4096)),
    ("nvc_analysis",             41966, (4030, 4296, 5519),   (398, 913, 5000)),
    ("delphi",                   45000, (1350, 1700, 2100),   (435, 780, 2500)),
    ("extraction_entity",        11198, (2817, 6393, 11136),  (693, 2904, 4096)),
    ("extraction_semantic",      11125, (3688, 7344, 12008),  (214, 533, 8192)),
    ("entity_profile_full",       5947, (2752, 4486, 6169),   (1249, 1717, 3018)),
    ("entity_update",             2638, (4877, 6147, 7505),   (1544, 2293, 5000)),
]
_THEEYE_WEIGHTS = [t[1] for t in THEEYE_TASKS]


def _lognorm_sample(mean: float, p95: float, hard_max: float) -> int:
    """Draw a token count from a lognormal fit to (mean, p95), clamped to hard_max.

    Token-length distributions are right-skewed, so a lognormal reproduces the
    long tail far better than a normal. sigma/mu are solved from the mean and
    p95 (z=1.6449); the quadratic is clamped so an extreme p95/mean ratio can't
    blow up.
    """
    mean = max(1.0, float(mean))
    p95 = max(mean, float(p95))
    r = math.log(p95 / mean)                 # >= 0
    disc = 1.6449 ** 2 - 2.0 * r
    sigma = 1.6449 if disc <= 0 else 1.6449 - math.sqrt(disc)
    sigma = min(max(sigma, 0.01), 2.0)
    mu = math.log(mean) - sigma * sigma / 2.0
    v = math.exp(mu + sigma * random.gauss(0.0, 1.0))
    return int(min(max(1, round(v)), hard_max))


def theeye_sample() -> tuple:
    """Sample one (input_tokens, output_tokens) request from the TheEye mix —
    weighted by each task's call rate, sized from its in/out distribution."""
    _n, _w, (im, ip, ix), (om, op, ox) = random.choices(THEEYE_TASKS, weights=_THEEYE_WEIGHTS, k=1)[0]
    return _lognorm_sample(im, ip, ix), _lognorm_sample(om, op, ox)


def _is_rejection(err: str) -> bool:
    """A clean 'too much load' rejection (429/503/at-capacity) vs a hard failure
    (timeout, connection error, 500). Distinguishes proper admission control
    (server refuses the overflow) from a server that breaks under overload.

    Matches the status via `client.generate`'s exact "HTTP {code}: ..." format
    (so a stray number in the body can't be mistaken for a 429), plus a few
    unambiguous rejection phrases.
    """
    e = (err or "").lower()
    if "http 429" in e or "http 503" in e:
        return True
    return any(s in e for s in ("at capacity", "rate limit",
                                "too many requests", "overloaded"))


async def soak_test(client: LLMClient, model: Optional[str], *, concurrency: int,
                    ctx_tokens: int, gen_tokens: int, duration_s: float,
                    distinct_prefix: bool = True, force_output: bool = True,
                    report_interval: float = 5.0,
                    sampler: Optional[Callable[[], tuple]] = None,
                    on_progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Hold `concurrency` requests in flight for `duration_s` seconds and measure
    the SUSTAINED input/output token rate — i.e. tokens per hour the server (and
    its backends) actually delivers under continuous load.

    `concurrency` worker coroutines each fire requests back-to-back until the
    deadline, so exactly `concurrency` requests are in flight the whole time.
    Reports cumulative in/out/total tok/s (× 3600 → tokens/hour), req/s, latency,
    TPOT, error rate, and per-minute output tok/s to reveal any drift/throttling.
    Calls `on_progress(snapshot)` every `report_interval` seconds.
    """
    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")
    salt_tokens = 64
    start = time.perf_counter()
    deadline = start + max(1.0, duration_s)
    # Lightweight per-request records (no generated text kept, so a long run
    # doesn't balloon memory):
    #   (t_rel, ok, ptoks, ctoks, ttft, total_time, est, err, req_out)
    recs: list[tuple] = []

    def build_body(in_tokens: int) -> str:
        salt = (_unique_prefix(salt_tokens) + "\n") if distinct_prefix else ""
        budget = max(1, in_tokens - (salt_tokens if distinct_prefix else 0))
        return (salt + _filler(budget, lead=random.randint(0, 10 ** 9)) +
                "\n\nUsing the text above as context, write a long, detailed "
                "continuation. Keep writing and do not stop.")

    async def worker():
        while time.perf_counter() < deadline:
            # A sampler (e.g. the TheEye workload mix) picks a realistic (in, out)
            # size per request; otherwise every request is the fixed configured size.
            in_toks, out_toks = sampler() if sampler else (ctx_tokens, gen_tokens)
            r = await client.generate(model=model, prompt=build_body(in_toks),
                                      max_tokens=out_toks, force_output=force_output)
            recs.append((time.perf_counter() - start, r.ok, r.prompt_tokens,
                         r.completion_tokens, r.ttft, r.total_time, r.est_tokens,
                         "" if r.ok else r.error, out_toks))
            if not r.ok:
                # Back off on failure so a down / at-capacity server isn't hammered
                # in a tight loop (which would also grow `recs` without bound).
                await asyncio.sleep(0.5)

    def snapshot() -> dict:
        el = max(time.perf_counter() - start, 1e-9)
        ok = [x for x in recs if x[1]]
        tin = sum(x[2] for x in ok)
        tout = sum(x[3] for x in ok)
        errs = [x[7] for x in recs if not x[1]]
        rejected = sum(1 for e in errs if _is_rejection(e))   # clean 429/503 refusals
        hard_err = len(errs) - rejected                        # timeouts / connection / 500
        in_tps = tin / el
        out_tps = tout / el
        tpots = [(x[5] - x[4]) / (x[3] - 1) for x in ok if x[3] > 1 and x[5] > x[4]]
        # per-minute output tok/s time-series (stability / throttling). Each bucket
        # is divided by the seconds it actually spans, so the in-progress final
        # minute (and a sub-minute run) isn't understated by dividing by 60.
        buckets: dict = {}
        for x in ok:
            m = int(x[0] // 60)
            b = buckets.setdefault(m, [0, 0])
            b[0] += x[3]; b[1] += x[2]
        ms = sorted(buckets)
        # Fold a short partial trailing minute into the one before it. When the run
        # ends, workers stop launching at the deadline but the whole in-flight batch
        # (~concurrency requests) drains within a narrow final window; dividing that
        # full batch of tokens by only a second or two would spike the last point to
        # the ceiling. Merging keeps the tokens but under a sane 60 s divisor, so the
        # final plotted point reflects steady state instead of a draining artifact.
        if len(ms) >= 2 and (el - ms[-1] * 60.0) < 30.0:
            last, prev = ms[-1], ms[-2]
            buckets[prev][0] += buckets[last][0]
            buckets[prev][1] += buckets[last][1]
            del buckets[last]
            ms = ms[:-1]
        series = []
        for m in ms:
            secs = max(min(60.0, el - m * 60.0), 1e-9)
            series.append((m, buckets[m][0] / secs, buckets[m][1] / secs))
        return {
            "elapsed": el, "remaining": max(0.0, deadline - time.perf_counter()),
            "duration": duration_s, "concurrency": concurrency,
            "requests": len(recs), "success": len(ok), "errors": len(errs),
            "in_tokens": tin, "out_tokens": tout,
            "in_tps": in_tps, "out_tps": out_tps, "total_tps": in_tps + out_tps,
            "in_per_hour": in_tps * 3600.0, "out_per_hour": out_tps * 3600.0,
            "total_per_hour": (in_tps + out_tps) * 3600.0,
            "req_per_s": len(ok) / el,
            "tpot_ms": 1000.0 * statistics.mean(tpots) if tpots else 0.0,
            "lat_p50": _pct([x[5] for x in ok], 0.5),
            "lat_p95": _pct([x[5] for x in ok], 0.95),
            "est_frac": (sum(1 for x in ok if x[6]) / len(ok)) if ok else 0.0,
            "gen_actual": (tout / len(ok)) if ok else 0.0,
            "req_out_mean": (sum(x[8] for x in ok) / len(ok)) if ok else 0.0,
            # per-request under-generation: server delivered < half the requested
            # output (truncation), works for both fixed and mixed (sampled) sizes
            "undergen_frac": (sum(1 for x in ok if x[8] >= 8 and x[3] < 0.5 * x[8]) / len(ok))
                             if ok else 0.0,
            "rejected": rejected, "hard_err": hard_err,
            "rejected_frac": (rejected / len(recs)) if recs else 0.0,
            "hard_err_frac": (hard_err / len(recs)) if recs else 0.0,
            "error_samples": errs[:3],
            "series": series,
        }

    async def reporter():
        while time.perf_counter() < deadline:
            await asyncio.sleep(report_interval)
            if on_progress:
                on_progress(snapshot())

    workers = [asyncio.create_task(worker()) for _ in range(max(1, concurrency))]
    rep = asyncio.create_task(reporter())
    try:
        await asyncio.gather(*workers)
    finally:
        rep.cancel()
    return snapshot()


def _capacity_levels(max_conc: int) -> list[int]:
    """Concurrency ramp for the capacity test: 1, 2, 4, 8, … up to `max_conc`
    (inclusive). Doubling keeps the number of steps small while still bracketing
    the saturation knee closely enough."""
    max_conc = max(1, int(max_conc))
    levels = []
    c = 1
    while c < max_conc:
        levels.append(c)
        c *= 2
    levels.append(max_conc)
    return sorted(set(levels))


async def capacity_test(client: LLMClient, model: Optional[str], *,
                        max_conc: int = 64, ctx_tokens: int = 1000,
                        gen_tokens: int = 500, window_s: float = 40.0,
                        warmup_frac: float = 0.35, settle_s: float = 3.0,
                        target_per_min: float = 0.0, plateau_frac: float = 0.08,
                        plateau_patience: int = 2, distinct_prefix: bool = True,
                        force_output: bool = True, levels: Optional[list[int]] = None,
                        on_progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Find the endpoint's sustainable token *capacity* in tokens-per-minute by
    ramping concurrency and measuring the delivered rate at each step.

    For each concurrency level (1, 2, 4, …, `max_conc`) it holds that many
    requests continuously in flight for `window_s` seconds, ignores the first
    `warmup_frac` of the window (queue fill / cold KV cache), and measures the
    steady-state in/out/total tokens-per-minute over the remainder. It climbs
    until throughput plateaus (gain < `plateau_frac` for `plateau_patience`
    consecutive steps), the server starts rejecting (429/503) or hard-erroring,
    or the max level is reached. The **peak healthy total tok/min** is the
    reported capacity; the level achieving it is the recommended operating point.

    If `target_per_min > 0`, the run also yields a PASS/FAIL: does the measured
    peak capacity meet the required tokens-per-minute? Emits a snapshot after each
    step via `on_progress` (fields: `steps`, `current`, `peak`, `phase`, `status`).
    """
    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")

    ramp = sorted({c for c in (levels or _capacity_levels(max_conc)) if c >= 1})
    salt_tokens = 64

    def build_body(in_tokens: int) -> str:
        salt = (_unique_prefix(salt_tokens) + "\n") if distinct_prefix else ""
        budget = max(1, in_tokens - (salt_tokens if distinct_prefix else 0))
        return (salt + _filler(budget, lead=random.randint(0, 10 ** 9)) +
                "\n\nUsing the text above as context, write a long, detailed "
                "continuation. Keep writing and do not stop.")

    async def run_step(conc: int) -> dict:
        # (t_rel, ok, ptoks, ctoks, ttft, total_time, est, err, req_out)
        recs: list[tuple] = []
        start = time.perf_counter()
        deadline = start + max(1.0, window_s)

        async def worker():
            while time.perf_counter() < deadline:
                r = await client.generate(model=model, prompt=build_body(ctx_tokens),
                                          max_tokens=gen_tokens, force_output=force_output)
                recs.append((time.perf_counter() - start, r.ok, r.prompt_tokens,
                             r.completion_tokens, r.ttft, r.total_time, r.est_tokens,
                             "" if r.ok else r.error, gen_tokens))
                if not r.ok:
                    await asyncio.sleep(0.5)

        workers = [asyncio.create_task(worker()) for _ in range(conc)]
        await asyncio.gather(*workers)

        # Steady-state window: drop the warm-up head, then rate = tokens from
        # requests that COMPLETED inside [t0, t1] divided by that span.
        t0 = window_s * warmup_frac
        t1 = window_s
        span = max(t1 - t0, 1e-9)
        win = [x for x in recs if t0 <= x[0] <= t1]
        ok = [x for x in win if x[1]]
        errs_all = [x[7] for x in recs if not x[1]]              # errors over whole step
        rejected = sum(1 for e in errs_all if _is_rejection(e))
        hard_err = len(errs_all) - rejected
        tin = sum(x[2] for x in ok)
        tout = sum(x[3] for x in ok)
        in_pm = tin / span * 60.0
        out_pm = tout / span * 60.0
        tpots = [(x[5] - x[4]) / (x[3] - 1) for x in ok if x[3] > 1 and x[5] > x[4]]
        undergen = (sum(1 for x in ok if x[8] >= 8 and x[3] < 0.5 * x[8]) / len(ok)) if ok else 0.0
        est_frac = (sum(1 for x in ok if x[6]) / len(ok)) if ok else 0.0
        req_total = len(recs)
        rejected_frac = (rejected / req_total) if req_total else 0.0
        hard_err_frac = (hard_err / req_total) if req_total else 0.0
        # Mean duration of ALL successful requests this step (also the ones that
        # finished outside the window) — used to diagnose a too-short window.
        ok_all = [x for x in recs if x[1]]
        dur_mean = statistics.mean([x[5] for x in ok_all]) if ok_all else 0.0
        # A step is "healthy" (its rate is genuinely sustainable) only if the
        # server wasn't shedding load or truncating output to keep up.
        healthy = (len(ok) > 0 and rejected_frac < 0.05 and hard_err_frac < 0.02
                   and undergen < 0.30)
        return {
            "conc": conc, "in_per_min": in_pm, "out_per_min": out_pm,
            "total_per_min": in_pm + out_pm, "requests": req_total,
            "success": len(ok), "win_success": len(ok),
            "lat_p50": _pct([x[5] for x in ok], 0.5),
            "lat_p95": _pct([x[5] for x in ok], 0.95),
            "ttft_p95": _pct([x[4] for x in ok], 0.95),
            "tpot_ms": 1000.0 * statistics.mean(tpots) if tpots else 0.0,
            "rejected": rejected, "hard_err": hard_err,
            "rejected_frac": rejected_frac, "hard_err_frac": hard_err_frac,
            "undergen_frac": undergen, "est_frac": est_frac,
            "gen_actual": (tout / len(ok)) if ok else 0.0,
            "dur_mean": dur_mean,
            "healthy": healthy, "error_samples": errs_all[:3],
        }

    steps: list[dict] = []
    peak: Optional[dict] = None
    plateau = 0
    saturation = ""
    emit = on_progress or (lambda *_: None)

    for i, conc in enumerate(ramp):
        emit({"steps": steps, "current": None, "peak": peak, "phase": "step_start",
              "status": f"c={conc}: measuring {window_s:g}s window…", "conc": conc,
              "target_per_min": target_per_min})
        if settle_s > 0 and i > 0:
            await asyncio.sleep(settle_s)
        s = await run_step(conc)
        steps.append(s)

        if not s["healthy"]:
            # Server is rejecting / erroring / truncating — we're past capacity.
            if s["rejected_frac"] >= 0.05:
                saturation = (f"server started rejecting (429/503) at c={conc} "
                              f"({s['rejected_frac'] * 100:.0f}% refused) — admission limit reached")
            elif s["hard_err_frac"] >= 0.02:
                saturation = (f"hard errors/timeouts at c={conc} "
                              f"({s['hard_err_frac'] * 100:.0f}%) — endpoint overloaded")
            elif s["success"] == 0:
                # Nothing finished inside the measurement window — the window is
                # shorter than a single request, not a capacity problem.
                dur = f" (a request takes ~{s['dur_mean']:.0f}s vs {window_s:g}s window)" \
                      if s["dur_mean"] else ""
                saturation = (f"no request finished inside the measurement window at "
                              f"c={conc}{dur} — raise ‘Window / step’ above the request duration")
            else:
                saturation = (f"output truncated at c={conc} "
                              f"(under-gen {s['undergen_frac'] * 100:.0f}%) — decode saturated")
        else:
            if peak is None or s["total_per_min"] > peak["total_per_min"] * (1 + plateau_frac):
                peak = s
                plateau = 0
            else:
                if s["total_per_min"] > peak["total_per_min"]:
                    peak = s   # marginal gain still counts as the best number
                plateau += 1
                if plateau >= plateau_patience:
                    saturation = (f"throughput plateaued at c={conc} — adding concurrency "
                                  f"stopped raising tok/min (< {plateau_frac * 100:.0f}% gain)")

        # Emit AFTER the peak/saturation update so the snapshot's `peak` is
        # current (a step-behind peak would lag the live readout by one level).
        emit({"steps": steps, "current": s, "peak": peak, "phase": "step_done",
              "status": f"c={conc}: {s['total_per_min']:,.0f} tok/min", "conc": conc,
              "target_per_min": target_per_min})
        if saturation:
            break

    if not saturation:
        last = ramp[-1] if ramp else 0
        saturation = (f"still climbing at the max concurrency c={last} — raise "
                      f"‘Max concurrency’ to find the true ceiling")

    peak_pm = peak["total_per_min"] if peak else 0.0
    result = {
        "model": model, "steps": steps, "peak": peak, "ramp": ramp,
        "window_s": window_s, "warmup_frac": warmup_frac,
        "ctx_tokens": ctx_tokens, "gen_tokens": gen_tokens,
        "peak_total_per_min": peak_pm,
        "peak_in_per_min": peak["in_per_min"] if peak else 0.0,
        "peak_out_per_min": peak["out_per_min"] if peak else 0.0,
        "peak_conc": peak["conc"] if peak else 0,
        "saturation": saturation,
        "target_per_min": target_per_min,
        "target_met": (peak_pm >= target_per_min) if target_per_min > 0 else None,
        "phase": "done", "status": "done",
    }
    emit(result)
    return result


# ---------------------------------------------------------------------------
# Model-fit suitability probe (Openclaw / Hermes)
#
# Instead of raw throughput, this scores whether a model can actually do the
# things an agentic caller needs: emit valid Hermes-style tool calls, pick the
# right tool with the right arguments, NOT call a tool when it shouldn't, return
# strict JSON, and follow tight formatting instructions. Each dimension yields a
# 0..1 score; a weighted blend plus hard gates give a SOBIB / PIIRIPEAL / EI SOBI
# verdict. Deterministic (temperature 0) so re-runs are comparable.
# ---------------------------------------------------------------------------

_HERMES_SYSTEM = (
    "You are a function calling AI model. You are provided with function signatures "
    "within <tools></tools> XML tags. You may call one or more functions to assist "
    "with the user query. Don't make assumptions about what values to plug into "
    "functions; if the user query does not require a tool, just answer normally. "
    "Here are the available tools:\n<tools>\n"
    '{"type":"function","function":{"name":"get_weather","description":"Get the current weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"},"unit":{"type":"string","enum":["celsius","fahrenheit"]}},"required":["city"]}}}\n'
    '{"type":"function","function":{"name":"web_search","description":"Search the web for up-to-date information","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}}\n'
    '{"type":"function","function":{"name":"calculator","description":"Evaluate an arithmetic expression","parameters":{"type":"object","properties":{"expression":{"type":"string"}},"required":["expression"]}}}\n'
    '{"type":"function","function":{"name":"send_email","description":"Send an email to a recipient","parameters":{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["to","subject","body"]}}}\n'
    "</tools>\n"
    "For each function call return a json object with function name and arguments "
    "within <tool_call></tool_call> XML tags as follows:\n"
    '<tool_call>\n{"name": <function-name>, "arguments": <args-dict>}\n</tool_call>'
)

# tool=None → the model should answer directly and NOT call a tool.
_TOOL_CASES = [
    {"user": "What's the weather in Tallinn right now? Use celsius.",
     "tool": "get_weather", "args": {"city": "tallinn", "unit": "celsius"}},
    {"user": "How warm is it in Tokyo at the moment, in fahrenheit?",
     "tool": "get_weather", "args": {"city": "tokyo", "unit": "fahrenheit"}},
    {"user": "Search the web for the latest news about the Estonian economy.",
     "tool": "web_search", "args": {"query": "eston"}},
    {"user": "Look online for information about the James Webb Space Telescope.",
     "tool": "web_search", "args": {"query": "webb"}},
    # Explicitly ask for the tool — otherwise a capable model reasonably computes
    # simple arithmetic itself (answers directly), which isn't a tool-calling miss.
    {"user": "Use the calculator tool to compute 2348 multiplied by 19.",
     "tool": "calculator", "args": {"expression": ["2348", "19"]}},
    {"user": "Use the calculator tool to compute 145 + 998 - 37.",
     "tool": "calculator", "args": {"expression": ["145", "998", "37"]}},
    {"user": "Send an email to john@example.com with the subject Lunch asking if "
             "he is free at noon tomorrow.",
     "tool": "send_email", "args": {"to": "john@example.com", "subject": "lunch"}},
    {"user": "Hi there! Can you briefly introduce yourself in one sentence?", "tool": None},
    {"user": "Write a short two-line poem about the sea.", "tool": None},
    {"user": "What does the word 'ephemeral' mean?", "tool": None},
]

# Native OpenAI function-calling schemas — the actual `tools` request parameter
# every router / vLLM / TGI / SGLang implements, as opposed to the Hermes prompt
# convention. Same four tools as _HERMES_SYSTEM, so the _TOOL_CASES apply to both.
_NATIVE_TOOLS = [
    {"type": "function", "function": {"name": "get_weather",
        "description": "Get the current weather for a city", "parameters": {"type": "object",
        "properties": {"city": {"type": "string"},
                       "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}},
        "required": ["city"]}}},
    {"type": "function", "function": {"name": "web_search",
        "description": "Search the web for up-to-date information", "parameters": {"type": "object",
        "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "calculator",
        "description": "Evaluate an arithmetic expression", "parameters": {"type": "object",
        "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "send_email",
        "description": "Send an email to a recipient", "parameters": {"type": "object",
        "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                       "body": {"type": "string"}}, "required": ["to", "subject", "body"]}}},
]
# Single-tool schema for the Provider-fit native probe.
_NATIVE_TOOL_SCHEMA = _NATIVE_TOOLS[0]
# Minimal system prompt for the native-tools test (the schema is in `tools`, so no
# tool definitions need to go in the prompt — unlike the Hermes convention).
_TOOL_SYSTEM = ("You are a helpful assistant with access to tools. Call the appropriate "
                "tool when the user's request needs one; otherwise answer normally.")

_JSON_SYSTEM = ("You output only raw JSON that satisfies the request. No prose, no "
                "explanation, no markdown, no code fences — just the JSON value.")

_JSON_CASES = [
    {"user": "Give a JSON object describing a fictional person with keys: name "
             "(string), age (integer), hobbies (array of strings).",
     "keys": {"name": str, "age": int, "hobbies": list}},
    {"user": "Return a JSON object with keys city (string), population (integer) and "
             "country (string) for a made-up town.",
     "keys": {"city": str, "population": int, "country": str}},
    {"user": "Output a JSON array of exactly three integers.",
     "array_of": int, "length": 3},
    {"user": 'Return a JSON object with keys "ok" (boolean true) and "items" (an '
             "array of three short strings).",
     "keys": {"ok": bool, "items": list}},
]

_INSTRUCT_SYSTEM = "You are a helpful assistant. Follow the user's formatting instructions exactly."

_INSTRUCT_CASES = [
    {"user": "Reply with exactly the single word READY and nothing else.",
     "check": lambda t: t.strip().strip(".").upper() == "READY"},
    {"user": "List three primary colours, one per line, with no numbering, bullets "
             "or any other text.",
     "check": lambda t: len([ln for ln in t.strip().splitlines() if ln.strip()]) == 3},
    {"user": "Answer in exactly one word: what is the capital of France?",
     "check": lambda t: len(t.strip().split()) == 1 and "paris" in t.lower()},
    {"user": "Respond with only the number 42 and nothing else.",
     "check": lambda t: t.strip().strip(".") == "42"},
]

# Reasoning / scaffolding that leaked into the visible answer — an agentic caller
# parses the raw output, so leaked chain-of-thought breaks it.
_LEAK_RE = re.compile(r"</?think>|<\|.*?\|>|^\s*(okay|let me|the user|i need to|first,)",
                      re.IGNORECASE | re.MULTILINE)

# Capture the payload between the tags (not up to the first brace) so nested
# argument objects survive; the closing tag, not a brace, is the delimiter.
_TOOLCALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _extract_tool_calls(text: str) -> list:
    """Pull (name, arguments) pairs out of a Hermes-style response.

    Accepts the canonical <tool_call>{...}</tool_call> wrapper and, as a
    fallback, a bare top-level JSON object carrying name/arguments (some models
    drop the tags). Returns [] when nothing parseable is present.
    """
    calls = []
    blocks = _TOOLCALL_RE.findall(text or "")
    if not blocks:
        # Fallback: a lone JSON object with name+arguments and no wrapper tags.
        m = re.search(r'\{[^{}]*"name"\s*:.*\}', text or "", re.DOTALL)
        if m:
            blocks = [m.group(0)]
    for b in blocks:
        try:
            obj = json.loads(b)
        except Exception:
            continue
        if isinstance(obj, dict) and "name" in obj:
            calls.append((str(obj.get("name")),
                          obj.get("arguments") or obj.get("parameters") or {}))
    return calls


def _arg_ok(args: dict, expected: dict) -> bool:
    """Every expected arg's substring(s) appear in the serialised arguments."""
    blob = json.dumps(args, ensure_ascii=False).lower() if isinstance(args, dict) else str(args).lower()
    for _k, want in expected.items():
        needles = want if isinstance(want, list) else [want]
        if not all(str(n).lower() in blob for n in needles):
            return False
    return True


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


async def suitability_test(client: LLMClient, model: Optional[str], *,
                           dims: Optional[list] = None,
                           on_progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Score a model's fit for agentic use (Hermes tool-calling + JSON + format).

    `dims` selects which of "tool" / "json" / "instruct" / "latency" to run
    (default: all four). Streams per-case results via `on_progress(event)` and
    returns a report dict with per-dimension scores and an overall verdict.
    Naturally cancellable — cancelling the task raises at the next await.
    """
    dims = dims or ["tool", "json", "instruct", "latency"]
    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")

    def emit(evt: dict):
        if on_progress:
            on_progress(evt)

    lat_samples: list = []       # (total_time, output_tps) across every probe
    cases_out: list = []         # flat per-case log for the results table

    async def ask(system, user, max_tokens, tools=None, tool_choice=None):
        r = await client.generate(model=model, prompt=user, system=system,
                                  max_tokens=max_tokens, temperature=0.0,
                                  tools=tools, tool_choice=tool_choice, retries=_PROBE_RETRIES)
        if r.ok and r.total_time > 0:
            lat_samples.append((r.total_time, r.output_tps))
        return r

    report: dict = {"model": model, "dims": list(dims)}

    # ---- Dimension: tool-calling ---------------------------------------------
    # Tests the native OpenAI `tools` API (the real standard), with a neutral
    # system prompt — deliberately NOT the Hermes prompt, which would instruct the
    # model into a convention it may be bad at (that's exactly what falsely failed
    # native-capable models before). As a best-effort fallback we still accept a
    # Hermes <tool_call> if the model emits one in its text on its own; a model is
    # credited if it calls the tool either way, and only one that does neither
    # scores zero.
    if "tool" in dims:
        pos = [c for c in _TOOL_CASES if c["tool"]]
        neg = [c for c in _TOOL_CASES if not c["tool"]]
        valid = select = args_ok = false_call = 0
        for c in _TOOL_CASES:
            r = await ask(_TOOL_SYSTEM, c["user"], 512,
                          tools=_NATIVE_TOOLS, tool_choice="auto")
            calls = (r.tool_calls or _extract_tool_calls(r.text)) if r.ok else []
            if c["tool"]:
                got = calls[0][0] if calls else None
                got_args = calls[0][1] if calls else {}
                v = bool(calls)
                s = v and got == c["tool"]
                a = s and _arg_ok(got_args, c["args"])
                valid += v; select += s; args_ok += a
                ok = a
                if ok:
                    detail = f"✓ {got} {got_args}"
                elif not r.ok:
                    # Full error (not truncated) so double-clicking the row reveals
                    # the whole server message — e.g. exactly why a 503 was returned.
                    detail = f"→ request failed: {r.error}"
                elif not calls:
                    # Nothing extractable — show what the model actually said, so a
                    # failure is self-diagnosing instead of just "→ ∅".
                    snippet = _raw_snippet(r.text)
                    detail = f"→ no tool call — model said: {snippet}" if snippet else "→ no tool call — empty response"
                elif got != c["tool"]:
                    detail = f"→ wrong tool: {got} (expected {c['tool']})"
                else:
                    detail = f"→ {got} — bad args: {got_args}"
            else:
                fc = bool(calls)
                false_call += fc
                ok = not fc
                detail = "answered directly" if ok else f"✗ spurious {calls[0][0]}"
            cases_out.append(("tool", c["user"], ok, detail))
            emit({"event": "case", "dim": "tool", "user": c["user"], "ok": ok, "detail": detail})
        npos, nneg = max(1, len(pos)), max(1, len(neg))
        report["tool"] = {
            "valid_rate": valid / npos, "select_rate": select / npos,
            "arg_rate": args_ok / npos, "falsecall_rate": false_call / nneg,
            "n_pos": len(pos), "n_neg": len(neg),
        }
        emit({"event": "dim_done", "dim": "tool", "score": _tool_score(report["tool"])})

    # ---- Dimension: strict JSON output ---------------------------------------
    if "json" in dims:
        parse_ok = schema_ok = 0
        for c in _JSON_CASES:
            r = await ask(_JSON_SYSTEM, c["user"], 384)
            obj = None
            if r.ok:
                try:
                    obj = json.loads(_strip_fences(r.text))
                except Exception:
                    obj = None
            p = obj is not None
            s = p and _json_schema_ok(obj, c)
            parse_ok += p; schema_ok += s
            detail = "schema ok" if s else ("parsed, schema off" if p else "not valid JSON")
            cases_out.append(("json", c["user"], s, detail))
            emit({"event": "case", "dim": "json", "user": c["user"], "ok": s, "detail": detail})
        n = max(1, len(_JSON_CASES))
        report["json"] = {"parse_rate": parse_ok / n, "schema_rate": schema_ok / n}
        # Stream the same blended score that ends up in report["scores"], so the
        # live per-dimension number matches the final scorecard.
        emit({"event": "dim_done", "dim": "json",
              "score": 0.5 * report["json"]["parse_rate"] + 0.5 * report["json"]["schema_rate"]})

    # ---- Dimension: instruction following / format discipline ----------------
    if "instruct" in dims:
        follow = leak = 0
        for c in _INSTRUCT_CASES:
            r = await ask(_INSTRUCT_SYSTEM, c["user"], 96)
            f = bool(r.ok and c["check"](r.text))
            lk = bool(r.ok and _LEAK_RE.search(r.text))
            follow += f; leak += lk
            detail = "followed" if f else f"deviated: {(r.text or '').strip()[:40]!r}"
            cases_out.append(("instruct", c["user"], f, detail))
            emit({"event": "case", "dim": "instruct", "user": c["user"], "ok": f, "detail": detail})
        n = max(1, len(_INSTRUCT_CASES))
        report["instruct"] = {"follow_rate": follow / n, "leak_rate": leak / n}
        emit({"event": "dim_done", "dim": "instruct",
              "score": max(0.0, report["instruct"]["follow_rate"]
                           - 0.5 * report["instruct"]["leak_rate"])})

    # ---- Latency (measured across every probe above) -------------------------
    if lat_samples:
        lats = sorted(t for t, _ in lat_samples)
        report["latency"] = {
            "mean_s": statistics.mean(lats),
            "p95_s": _pct(lats, 0.95),
            "mean_out_tps": statistics.mean([o for _, o in lat_samples if o] or [0.0]),
            "n": len(lat_samples),
        }

    report["cases"] = cases_out
    report["scores"] = _suitability_scores(report)
    report["overall"], report["verdict"] = _suitability_verdict(report)
    emit({"event": "done", "report": report})
    return report


def _tool_score(t: dict) -> float:
    # Reward valid+correct+args, penalise spurious calls; weighted to correctness.
    return max(0.0, 0.30 * t["valid_rate"] + 0.30 * t["select_rate"]
               + 0.25 * t["arg_rate"] + 0.15 * (1.0 - t["falsecall_rate"]))


def _json_schema_ok(obj, case: dict) -> bool:
    if "keys" in case:
        if not isinstance(obj, dict):
            return False
        for k, typ in case["keys"].items():
            if k not in obj:
                return False
            v = obj[k]
            if typ is int and isinstance(v, bool):   # bool is a subclass of int
                return False
            if not isinstance(v, typ):
                return False
        return True
    if "array_of" in case:
        if not isinstance(obj, list):
            return False
        if "length" in case and len(obj) != case["length"]:
            return False
        return all(isinstance(x, case["array_of"]) and not isinstance(x, bool) for x in obj)
    return False


def _suitability_scores(report: dict) -> dict:
    out = {}
    if "tool" in report:
        out["tool"] = _tool_score(report["tool"])
    if "json" in report:
        out["json"] = 0.5 * report["json"]["parse_rate"] + 0.5 * report["json"]["schema_rate"]
    if "instruct" in report:
        out["instruct"] = max(0.0, report["instruct"]["follow_rate"]
                              - 0.5 * report["instruct"]["leak_rate"])
    return out


def _suitability_verdict(report: dict) -> tuple:
    scores = report["scores"]
    weights = {"tool": 0.5, "json": 0.25, "instruct": 0.25}
    wsum = sum(weights[k] for k in scores) or 1.0
    overall = sum(scores[k] * weights[k] for k in scores) / wsum
    # Hard gate: if tool-calling is fundamentally broken, it can't be agentic-fit
    # no matter how clean its prose JSON is.
    if "tool" in report and report["tool"]["valid_rate"] < 0.5:
        return overall, "❌ EI SOBI — ei suuda usaldusväärselt tööriistu kutsuda"
    if overall >= 0.85:
        return overall, "✅ SOBIB — täidab agentse kasutuse nõuded"
    if overall >= 0.6:
        return overall, "⚠ PIIRIPEAL — kasutatav, aga esineb vigu; kontrolli nõrku dimensioone"
    return overall, "❌ EI SOBI — liiga palju vigu agentseks kasutuseks"


# ---------------------------------------------------------------------------
# Provider readiness (OpenRouter / HuggingFace) + bottleneck analysis
#
# Answers "could this backend serve real router/inference-provider traffic, and
# where does it break first?". Two phases:
#   1. API-contract compliance — the hard requirements a provider imposes
#      (streaming, usage accounting, max_tokens/stop honouring, deterministic
#      greedy decode, sampling params applied, concurrent correctness, clean 4xx
#      errors). Each is a single pass/fail probe mapped to the requirement.
#   2. Concurrency sweep — replay increasingly parallel load at a realistic
#      request shape, measure output tok/s, TTFT p95, TPOT and the 429-vs-hard
#      error split at each level, then classify the dominant bottleneck (queue/
#      prefill-bound, decode-bound, no batching, admission control, or breaks).
# A weighted gate over both phases yields a SOBIB/PIIRIPEAL/EI SOBI verdict for
# OpenRouter and for HuggingFace, whose emphases differ (usage/billing & TTFT vs
# throughput/batching).
# ---------------------------------------------------------------------------

def _classify_load_errors(errors: list) -> tuple:
    """Split a load run's error strings into clean rejections vs hard failures."""
    rej = sum(1 for e in errors if _is_rejection(e))
    return rej, len(errors) - rej


# Token budget for a correctness probe against a reasoning model — the hidden
# chain-of-thought has to finish before the visible answer appears, so a 16-token
# budget would be spent entirely on reasoning.
_REASONING_ANSWER_TOKENS = 2048
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)


def _visible_answer(r) -> str:
    """The visible answer, with <think>…</think> reasoning stripped. Also handles
    an UNCLOSED <think> (the token budget ran out mid-thought, common when a probe
    doesn't give a reasoning model enough room) by dropping everything from the
    opening tag on — otherwise a truncated chain-of-thought would be scored as the
    answer. Harmless for non-reasoning models (no tag → text as-is)."""
    text = _THINK_RE.sub("", (r.text or ""))
    m = _THINK_OPEN_RE.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()


def _raw_snippet(text: str, n: int = 70) -> str:
    """A collapsed, truncated preview of raw model output for a failed probe's
    detail line — so a run is self-diagnosing instead of just showing "nothing
    extracted" with no clue why."""
    t = " ".join((text or "").split())
    if not t:
        return ""
    return repr(t[:n] + ("…" if len(t) > n else ""))


async def _detect_reasoning(client: LLMClient, model: str) -> bool:
    """Is this a reasoning model? True if the server returns reasoning_content, emits
    an (even unclosed) <think> tag in content — the common local-server style (vLLM /
    llama.cpp / SGLang inline the chain-of-thought with no separate field) — or
    generates tokens while leaving the visible answer empty (thinking ate the
    budget). Lets the probes give the model room to finish and reach an answer."""
    r = await client.generate(model=model, prompt="Reply with the single word: pong.",
                              max_tokens=64, temperature=0.0, retries=_PROBE_RETRIES)
    if not r.ok:
        return False
    if (r.reasoning or "").strip():
        return True
    if _THINK_OPEN_RE.search(r.text or ""):
        return True
    return bool(r.completion_tokens > 8 and not _visible_answer(r))


async def _readiness_compliance(client: LLMClient, model: str,
                                emit: Callable[[dict], None],
                                reasoning: bool = False) -> list:
    """Run the API-contract probes; return a list of {name, ok, detail, req}.
    When `reasoning` is set, content-dependent probes get a large token budget and
    strip <think> so a thinking model can actually reach its visible answer."""
    checks: list = []

    def add(name, ok, detail, req):
        row = {"name": name, "ok": bool(ok), "detail": str(detail)[:80], "req": req}
        checks.append(row)
        emit({"event": "check", **row})

    async def one(prompt, max_tokens, **kw):
        return await client.generate(model=model, prompt=prompt, max_tokens=max_tokens,
                                     retries=_PROBE_RETRIES, **kw)

    # On a reasoning model, answer probes need room for the chain-of-thought.
    def budget(base):
        return _REASONING_ANSWER_TOKENS if reasoning else base

    # 1. Basic chat completion.
    r = await one("Reply with the single word: pong.", budget(16), temperature=0.0)
    ans = _visible_answer(r)
    add("Chat endpoint", r.ok and bool(ans),
        (ans[:40] if r.ok else r.error) or "(empty content — reasoning only?)",
        "OpenAI /v1/chat/completions returns a completion")

    # 2 + 3. Streaming and usage accounting (one longer request tells us both).
    r = await one(LONG_PROMPT, 48, temperature=0.0)
    streamed = bool(r.ok and r.completion_tokens > 3 and r.ttft < r.total_time - 1e-3)
    add("Streaming (SSE)", streamed,
        (f"TTFT {r.ttft * 1000:.0f}ms of {r.total_time * 1000:.0f}ms total"
         if r.ok else r.error) if streamed else "response not streamed token-by-token",
        "stream:true delivers tokens incrementally")
    add("Usage accounting", r.ok and not r.est_tokens,
        "server returned prompt/completion token counts" if (r.ok and not r.est_tokens)
        else "no usage block — counts estimated",
        "usage in final SSE chunk (routers bill on it)")

    # 4 + 5. max_tokens honoured and finish_reason on truncation.
    cap = 16
    r = await one(LONG_PROMPT, cap, temperature=0.0)
    respected = bool(r.ok and 0 < r.completion_tokens <= cap + 8)
    add("max_tokens honored", respected,
        f"{r.completion_tokens} tokens for max_tokens={cap}" if r.ok else r.error,
        "generation stops at max_tokens")
    add("finish_reason=length", bool(r.ok and r.finish_reason == "length"),
        f"finish_reason={r.finish_reason or '∅'}" if r.ok else r.error,
        "correct finish_reason when truncated")

    # 6. Stop sequences.
    r = await one("Output exactly these words separated by single spaces and nothing "
                  "else: alpha bravo charlie delta echo", budget(48), temperature=0.0,
                  stop=["charlie"])
    txt = _visible_answer(r).lower()
    stop_ok = bool(r.ok and "alpha" in txt and "delta" not in txt and "echo" not in txt)
    add("Stop sequences", stop_ok,
        (repr(_visible_answer(r)[:40]) if r.ok else r.error) if not stop_ok
        else "cut off at the stop token",
        "honors the stop parameter")

    # 7. Determinism at temperature 0 (greedy decode should be reproducible).
    a = await one("Name three colours, comma separated, lowercase.", budget(24), temperature=0.0)
    b = await one("Name three colours, comma separated, lowercase.", budget(24), temperature=0.0)
    det = bool(a.ok and b.ok and _visible_answer(a)[:60] == _visible_answer(b)[:60])
    add("Deterministic (temp 0)", det,
        "identical output on repeat" if det else "temp-0 output varied between calls",
        "reproducible greedy decoding")

    # 8. Sampling params actually take effect.
    a = await one("Write one short sentence about the sea.", budget(32), temperature=1.0,
                  top_p=0.95, seed=1)
    b = await one("Write one short sentence about the sea.", budget(32), temperature=1.0,
                  top_p=0.95, seed=2)
    varies = bool(a.ok and b.ok and _visible_answer(a) != _visible_answer(b))
    add("Sampling params applied", varies,
        "temperature/seed produce varied output" if varies
        else "identical output despite temp/seed change",
        "honors temperature / top_p / seed")

    # 9. Concurrent correctness — a small parallel burst all succeeds.
    burst = await asyncio.gather(
        *(one(f"[{i}] Reply with the single word OK.", 8, temperature=0.0) for i in range(8)))
    nok = sum(1 for x in burst if x.ok)
    add("Concurrent requests", nok == len(burst),
        f"{nok}/{len(burst)} parallel requests ok",
        "handles simultaneous requests correctly")

    # 10. Clean error on an invalid request (rather than a 5xx or a hang).
    r = await client.generate(model="__llmscanner_nonexistent_model__",
                              prompt="hi", max_tokens=8, retries=_PROBE_RETRIES)
    clean = bool((not r.ok) and r.error.startswith("HTTP 4"))
    add("Clean error on bad request", clean,
        (r.error[:60] if not r.ok else "accepted an invalid model — no validation"),
        "returns a 4xx JSON error, not 5xx / timeout")

    # 11. Tool calling (native API) — the standard OpenAI `tools` request parameter
    #     (structured schema in, `tool_calls` in the response), which is what
    #     OpenRouter / vLLM / TGI / SGLang actually implement — the real API contract
    #     a provider must satisfy. Gates the verdicts. The legacy /v1/completions
    #     endpoint has no tools API, so it's n/a there.
    native_ok = False
    native_na = client.endpoint == "completions"
    if native_na:
        add("Tool calling (native API)", True,
            "n/a — /v1/completions has no tools API (use the chat endpoint)",
            "supports the standard OpenAI `tools` request parameter")
    else:
        r = await client.generate(
            model=model, prompt="What's the weather in Tallinn right now? Use celsius.",
            max_tokens=budget(128), temperature=0.0,
            tools=[_NATIVE_TOOL_SCHEMA], tool_choice="auto", retries=_PROBE_RETRIES)
        native_ok = bool(r.ok and r.tool_calls and r.tool_calls[0][0] == "get_weather")
        if native_ok:
            ndetail = f"✓ {r.tool_calls[0][0]}({r.tool_calls[0][1]})"
        elif not r.ok:
            ndetail = f"request failed: {r.error[:60]}"
        elif r.tool_calls:
            ndetail = f"called wrong tool: {r.tool_calls[0][0]}"
        else:
            snippet = _raw_snippet(_visible_answer(r))
            ndetail = f"no tool_calls — model said: {snippet}" if snippet else "no tool_calls, empty response"
        add("Tool calling (native API)", native_ok, ndetail,
            "supports the standard OpenAI `tools` request parameter")

    # 11b. Tool calling (Hermes prompt) — a FALLBACK, only checked when the native
    #      API above didn't work. A model that does native tool-calling doesn't need
    #      the prompt-embedded Hermes/NousResearch <tool_call> XML convention (used by
    #      some fine-tunes / agent frameworks like Openclaw), so we skip it and mark
    #      it n/a rather than showing a confusing red for a capable model.
    #      Informational either way — it never gates the verdicts.
    if native_ok or native_na:
        add("Tool calling (Hermes prompt)", True,
            "n/a — native tool-calling works" if native_ok else "n/a — completions endpoint",
            "prompt-embedded fallback convention, only checked if native fails")
    else:
        tool_pos = [c for c in _TOOL_CASES if c["tool"]][:3]
        thits = 0
        sample = ""
        for c in tool_pos:
            r = await one(c["user"], budget(256), temperature=0.0, system=_HERMES_SYSTEM)
            calls = _extract_tool_calls(_visible_answer(r)) if r.ok else []
            if calls and calls[0][0] == c["tool"]:
                thits += 1
            elif not sample:
                # Nothing extractable — capture what the model actually said, so a
                # failure is self-diagnosing instead of a bare "0/3 correct".
                sample = _raw_snippet(_visible_answer(r)) if r.ok else repr(r.error[:60])
        tool_ok = thits >= max(1, len(tool_pos) - 1)   # allow one miss
        detail = f"{thits}/{len(tool_pos)} correct Hermes tool calls"
        if not tool_ok and sample:
            detail += f" — e.g. model said: {sample}"
        add("Tool calling (Hermes prompt)", tool_ok, detail,
            "prompt-embedded fallback convention (Hermes/NousResearch XML)")

    # 12. Structured output — HuggingFace also runs a structured-output test.
    json_cases = _JSON_CASES[:2]
    jhits = 0
    for c in json_cases:
        r = await one(c["user"], budget(384), temperature=0.0, system=_JSON_SYSTEM)
        obj = None
        if r.ok:
            try:
                obj = json.loads(_strip_fences(_visible_answer(r)))
            except Exception:
                obj = None
        if obj is not None and _json_schema_ok(obj, c):
            jhits += 1
    add("Structured output", jhits >= 1, f"{jhits}/{len(json_cases)} valid JSON schemas",
        "HF runs a structured-output test on LLMs")

    # 13. /v1/models metadata — both routers read pricing + context_length from
    #     /v1/models (OpenRouter model spec; HF :fastest/:cheapest selection).
    #     Locally, pricing is a router-side concern, so the pass condition is the
    #     context-length field servers actually expose; pricing is reported too.
    metas = await client.list_models_raw()
    meta = next((x for x in metas if x.get("id") == model), None) or (metas[0] if metas else {})
    ctx_keys = ("context_length", "max_model_len", "max_context_length",
                "max_seq_len", "max_position_embeddings")
    has_ctx = any(isinstance(meta.get(k), int) and meta[k] > 0 for k in ctx_keys)
    has_price = ("pricing" in meta) or ("price" in meta)
    add("/v1/models metadata", has_ctx,
        f"context_length {'✓' if has_ctx else '✗'} · pricing {'✓' if has_price else '✗ (router-side)'}",
        "expose context_length (+ pricing) via /v1/models")

    # 14. Auth enforcement — a provider endpoint routed public traffic must gate
    #     access by API key. Hit it with a deliberately-wrong key and expect a
    #     401/403. An open endpoint (bad key accepted) is fine for local dev but
    #     not for a live provider, so this is a non-critical gate.
    bad = LLMClient(client.host, client.port, api_key="llmscanner-invalid-key-9z9z9z",
                    scheme=client.scheme, base_path=client.base_path,
                    endpoint=client.endpoint, timeout=min(client.timeout, 20.0),
                    extra_body=client.extra_body)
    ra = await bad.generate(model=model, prompt="hi", max_tokens=4, retries=_PROBE_RETRIES)
    enforced = (not ra.ok) and any(ra.error.startswith(f"HTTP {c}") for c in ("401", "403"))
    if enforced:
        adetail = "bad API key rejected (401/403)"
    elif ra.ok:
        adetail = "OPEN — a bad API key was accepted (no auth enforced)"
    else:
        adetail = f"inconclusive: {ra.error[:50]}"
    add("Auth enforced", enforced, adetail, "provider must gate access by API key")

    emit({"event": "phase_done", "name": "compliance",
          "passed": sum(1 for c in checks if c["ok"]), "total": len(checks)})
    return checks


# Deterministic golden set for a quick quality floor — a genuinely-served,
# full-precision model of any reasonable size answers these; a silently quantised,
# distilled, wrong, or broken model starts dropping them. Not a definitive
# quantisation detector, but the first-pass eval a router runs before trusting a
# third-party backend's "quality".
_GOLDEN_CASES = [
    {"user": "What is the capital of France? Answer with one word.", "must": ["paris"]},
    {"user": "What is 17 multiplied by 23? Answer with the number only.", "must": ["391"]},
    {"user": "What is the chemical symbol for gold? Symbol only.", "must": ["au"]},
    {"user": "How many days are in a normal (non-leap) year? Number only.", "must": ["365"]},
    {"user": "What is the square root of 144? Number only.", "must": ["12"]},
    {"user": "Who wrote the play Romeo and Juliet? Surname only.", "must": ["shakespeare"]},
    {"user": "Continue the sequence with the next number: 2, 4, 8, 16. Number only.", "must": ["32"]},
    {"user": "In what year did the Second World War end? Year only.", "must": ["1945"]},
    {"user": "What is the boiling point of water in Celsius at sea level? Number only.", "must": ["100"]},
    {"user": "Translate the phrase 'thank you' into French.", "must": ["merci"]},
]


async def _readiness_integrity(client: LLMClient, model: str,
                               emit: Callable[[dict], None], *,
                               ctx_probe_tokens: int = 8192, in_tokens: int = 1024,
                               reasoning: bool = False) -> dict:
    """Adversarial honesty probes a router runs on a backend it does not control:
    is the reported token count honest (billing), is the advertised context real,
    and is the served model actually at the claimed quality (not silently quantised)?
    On a reasoning model, quality/recall probes use a large budget and strip <think>,
    and token honesty counts reasoning tokens (else a thinking model is falsely
    accused of billing inflation)."""
    out: dict = {}
    ct = [0, 0]  # [passed, total] for the phase summary
    ans_tokens = _REASONING_ANSWER_TOKENS if reasoning else None

    def add(name, ok, detail):
        ct[1] += 1
        ct[0] += 1 if ok else 0
        emit({"event": "check", "name": name, "ok": bool(ok),
              "detail": str(detail)[:90], "req": "integrity"})

    async def one(prompt, max_tokens, **kw):
        return await client.generate(model=model, prompt=prompt, max_tokens=max_tokens,
                                     retries=_PROBE_RETRIES, **kw)

    # 1. Token-count honesty — force a known output length, then compare the
    #    server's reported completion_tokens against a tokenizer-agnostic estimate
    #    from the actual text. A backend inflating counts to overbill shows a high
    #    reported/actual ratio. Only checkable when the server reports usage.
    r = await one(LONG_PROMPT, 128, force_output=True, temperature=0.0)
    if not r.ok:
        out["token_honesty"] = {"ok": False, "detail": r.error}
        add("Token-count honesty", False, r.error)
    elif r.est_tokens:
        out["token_honesty"] = {"ok": None, "detail": "no usage block — cannot verify"}
        add("Token-count honesty", True, "n/a — server sent no usage to verify")
    else:
        # Independent estimate = the most tokens we can actually see: streamed
        # chunks, or the text + reasoning length. Counting reasoning is essential —
        # a thinking model's tokens are real output even when `content` is empty.
        text_est = approx_tokens(r.text) + approx_tokens(r.reasoning or "")
        indep = max(r.stream_chunks, text_est, 1)
        ratio = r.completion_tokens / indep
        inflated = ratio > 1.5
        rnote = f", {approx_tokens(r.reasoning or '')} reasoning" if r.reasoning else ""
        detail = (f"reported {r.completion_tokens} vs ~{indep} generated "
                  f"({r.stream_chunks} chunks{rnote}); ×{ratio:.2f}")
        out["token_honesty"] = {"ok": not inflated, "ratio": ratio,
                                "reported": r.completion_tokens, "text_est": indep,
                                "chunks": r.stream_chunks, "detail": detail}
        add("Token-count honesty", not inflated, detail)

    # 2. Context honesty — hide a code deep in a long prompt near the advertised
    #    limit and ask for it back. Fails if the server truncates silently or the
    #    claimed context length isn't real.
    advertised = await client.model_max_len(model)
    size = min(ctx_probe_tokens, advertised) if advertised else ctx_probe_tokens
    nd = await needle_test(client, model, ctx_tokens=size, depths=(0.1, 0.5, 0.9),
                           answer_tokens=(ans_tokens or 32))
    ctx_ok = nd["passed"] >= 2
    detail = (f"recalled {nd['passed']}/{nd['total']} at ~{size} tok"
              + (f" (server claims {advertised})" if advertised else ""))
    out["context_honesty"] = {"ok": ctx_ok, "passed": nd["passed"], "total": nd["total"],
                              "size": size, "advertised": advertised, "detail": detail}
    add("Context honesty (recall)", ctx_ok, detail)

    # 3. Model quality / authenticity — a golden-answer eval. A grossly degraded
    #    (quantised/wrong/broken) model drops these.
    hits = 0
    for c in _GOLDEN_CASES:
        rr = await one(c["user"], ans_tokens or 24, temperature=0.0)
        txt = _visible_answer(rr).lower()
        if rr.ok and any(m in txt for m in c["must"]):
            hits += 1
    score = hits / len(_GOLDEN_CASES)
    q_ok = score >= 0.7
    out["quality"] = {"ok": q_ok, "score": score, "hits": hits, "total": len(_GOLDEN_CASES),
                      "detail": f"{hits}/{len(_GOLDEN_CASES)} golden answers ({score * 100:.0f}%)"}
    add("Model quality (golden eval)", q_ok, out["quality"]["detail"])

    # 4. Logprob fingerprint — the model's confidence on a trivial fact is a proxy
    #    for its precision; full-precision weights are very confident. Informational
    #    (many servers don't expose logprobs), so it never fails the phase.
    rr = await one("The capital of France is", 3, temperature=0.0, logprobs=True)
    if rr.logprob_avg is not None:
        p = math.exp(max(-20.0, rr.logprob_avg))
        out["logprob"] = {"supported": True, "avg": rr.logprob_avg,
                          "detail": f"mean logprob {rr.logprob_avg:.2f} (p≈{p:.2f}) on a trivial prompt"}
        add("Logprob fingerprint", True, out["logprob"]["detail"])
    else:
        out["logprob"] = {"supported": False, "detail": "logprobs unsupported — cannot fingerprint"}
        add("Logprob fingerprint", True, "logprobs unsupported (informational)")

    # 5. Cancellation / disconnect handling — measure probe TTFT, saturate the
    #    server with several long requests that disconnect after the first token,
    #    then re-probe TTFT. If the server honoured the disconnects (freed the
    #    slots, as it should when a router cancels a user's request), the probe
    #    stays fast; if it kept generating the abandoned requests, the probe
    #    queues behind them. Informational — timing-sensitive, so it never fails
    #    the phase, but a large blow-up is a real red flag.
    body = _filler(max(64, in_tokens // 4)) + "\n\nReply with the single word OK."
    base = await one(body, 8, temperature=0.0)
    if base.ok:
        long_body = _filler(in_tokens) + "\n\nWrite an extremely long essay and keep going."
        await asyncio.gather(*(client.stream_abort(model=model, prompt=f"[{i}] " + long_body,
                                                   max_tokens=1024) for i in range(4)))
        after = await one(body, 8, temperature=0.0)
        t_base, t_after = base.ttft, (after.ttft if after.ok else 0.0)
        ratio = t_after / max(t_base, 1e-3)
        cancel_ok = bool(after.ok and ratio <= 3.0)
        detail = (f"probe TTFT {t_base * 1000:.0f}ms → {t_after * 1000:.0f}ms after aborting 4 "
                  f"streams (×{ratio:.1f})")
        out["cancellation"] = {"ok": cancel_ok, "t_base": t_base, "t_after": t_after,
                               "ratio": ratio, "detail": detail}
        add("Cancellation handling", cancel_ok, detail)
    else:
        out["cancellation"] = {"ok": None, "detail": "baseline probe failed — skipped"}
        add("Cancellation handling", True, "baseline probe failed — skipped")

    emit({"event": "phase_done", "name": "integrity", "passed": ct[0], "total": ct[1]})
    return out


def _readiness_row(conc: int, stats: "LoadStats", *, overload: bool) -> dict:
    rej, hard = _classify_load_errors(stats.errors)
    req = max(1, stats.requests)
    return {
        "conc": conc, "overload": overload,
        "out_tps": stats.aggregate_tps, "in_tps": stats.input_tps,
        "total_tps": stats.total_tps, "ttft_p95": stats.ttft_p95,
        "ttft_p99": stats.ttft_p99, "lat_p99": stats.latency_p99,
        "lat_p95": stats.latency_p95, "tpot_ms": stats.tpot_ms,
        "req_per_s": stats.req_per_s, "success": stats.success,
        "requests": stats.requests, "rejected": rej, "hard_err": hard,
        "rej_frac": rej / req, "hard_frac": hard / req, "est_frac": stats.est_frac,
    }


def _readiness_analysis(rows: list) -> dict:
    """Locate the throughput bottleneck (from the main sweep) and, separately,
    grade admission control (from the overload probe) — the two are independent:
    a backend can shed overload cleanly yet still be decode-bound below it."""
    main = [r for r in rows if not r["overload"]] or rows
    ov = next((r for r in rows if r["overload"]), None)
    base, top = main[0], main[-1]
    peak_row = max(main, key=lambda r: r["out_tps"])
    peak = peak_row["out_tps"]
    # Knee = last level where output throughput still climbed >10% over the prior.
    knee = main[0]
    for prev, cur in zip(main, main[1:]):
        if cur["out_tps"] > prev["out_tps"] * 1.10:
            knee = cur
        else:
            break
    scale = peak / max(base["out_tps"], 1e-9)
    ttft_growth = top["ttft_p95"] / max(base["ttft_p95"], 1e-3)
    tpot_growth = (top["tpot_ms"] / base["tpot_ms"]) if base["tpot_ms"] > 0 else 1.0
    hard_max = max((r["hard_frac"] for r in rows), default=0.0)

    # Admission control — an independent dimension, judged from the overload row.
    if ov is None:
        admission, adm_txt = "none", ""
    elif ov["hard_frac"] >= 0.05:
        admission = "breaks"
        adm_txt = (f" · Overload c={ov['conc']}: BREAKS with {ov['hard_frac'] * 100:.0f}% "
                   "hard errors/timeouts instead of rejecting cleanly.")
    elif ov["rej_frac"] >= 0.02:
        admission = "clean"
        adm_txt = (f" · Overload c={ov['conc']}: excess rejected cleanly (429/503, "
                   f"{ov['rej_frac'] * 100:.0f}%) — proper backpressure.")
    else:
        admission = "absorbed"
        adm_txt = (f" · Overload c={ov['conc']}: absorbed +25% with no rejects "
                   "(headroom, or an unbounded queue — watch TTFT).")

    # Throughput bottleneck — from the main sweep only.
    if len(main) < 2:
        btype = "insufficient"
        text = ("ℹ Only one concurrency level tested — add more (e.g. 1,4,8,16) to locate a "
                f"bottleneck. Single point: {peak:,.0f} out tok/s at c={top['conc']}.")
    elif hard_max >= 0.05:
        btype = "stability"
        text = (f"❌ Breaks under load — up to {hard_max * 100:.0f}% hard errors/timeouts "
                "(not clean 429/503). The server or its queue collapses instead of shedding "
                "load; fix this first.")
    elif scale < 1.3:
        btype = "no-batching"
        text = (f"⚠ Throughput ceiling — output barely scales (×{scale:.1f} from c=1 to "
                f"c={top['conc']}). Little/no continuous batching: one request already "
                "saturates it, so parallel traffic just queues.")
    elif ttft_growth >= 3.0 and ttft_growth >= tpot_growth * 1.5:
        btype = "prefill"
        text = (f"⚠ Prefill / queue-bound — TTFT p95 grows ×{ttft_growth:.1f} under load "
                f"while per-token decode holds (×{tpot_growth:.1f}). Requests queue to start; "
                f"throughput is fine ({peak:,.0f} tok/s) but first-token latency degrades "
                f"past c={knee['conc']}.")
    elif tpot_growth >= 1.5:
        btype = "decode"
        text = (f"⚠ Decode-bound — time-per-output-token rises ×{tpot_growth:.1f} under load "
                f"(KV-cache / memory-bandwidth pressure). Cap concurrency near c={knee['conc']} "
                "or add capacity to hold latency.")
    else:
        btype = "healthy"
        text = (f"✅ Scales cleanly to c={top['conc']} (×{scale:.1f} throughput, TTFT "
                f"×{ttft_growth:.1f}, TPOT ×{tpot_growth:.1f}); peak {peak:,.0f} out tok/s.")
    return {
        "type": btype, "admission": admission, "text": (text + adm_txt).strip(),
        "peak_out_tps": peak, "peak_conc": peak_row["conc"], "knee_conc": knee["conc"],
        "ttft_p95_knee": knee["ttft_p95"], "ttft_p95_base": base["ttft_p95"], "scale": scale,
        "ttft_p99_knee": knee.get("ttft_p99", 0.0), "lat_p99_knee": knee.get("lat_p99", 0.0),
        "ttft_growth": ttft_growth, "tpot_growth": tpot_growth, "hard_frac_max": hard_max,
    }


def _rd_verdict(frac: float, critical_ok: bool, gates: dict, crit_msg: str) -> str:
    failed = [k for k, v in gates.items() if not v]
    if not critical_ok:
        return f"❌ EI SOBI — {crit_msg}"
    if frac >= 0.999:
        return "✅ SOBIB — täidab kõik nõuded"
    if frac >= 0.75:
        return "⚠ PIIRIPEAL — töötab, aga puudu: " + ", ".join(failed[:3])
    return "❌ EI SOBI — liiga palju puudujääke: " + ", ".join(failed[:3])


def _readiness_verdicts(report: dict) -> dict:
    checks = {c["name"]: c["ok"] for c in report["compliance"]}
    a = report["analysis"]
    sla = report["ttft_sla_s"]
    stable = a["type"] != "stability"
    ttft_ok = a["ttft_p95_knee"] <= sla

    def has(name):
        return checks.get(name, False)

    # Integrity (honesty) — token_honesty None means "couldn't verify" (no usage),
    # which is not a failure; only an explicit False (inflation detected) fails.
    integ = report.get("integrity", {})
    tok_honest = integ.get("token_honesty", {}).get("ok") is not False
    ctx_honest = integ.get("context_honesty", {}).get("ok", True)
    quality_ok = integ.get("quality", {}).get("ok", True)

    # OpenRouter: correctness + TTFT + clean backpressure; reads model metadata
    # (pricing, context_length) from /v1/models; and — critically for a router
    # billing on tokens — honest token counts.
    or_gates = {
        "Streaming (SSE)": has("Streaming (SSE)"),
        "Usage accounting": has("Usage accounting"),
        "Token-count honesty": tok_honest,
        "max_tokens honored": has("max_tokens honored"),
        "Stop sequences": has("Stop sequences"),
        "Concurrent requests": has("Concurrent requests"),
        "Clean errors": has("Clean error on bad request"),
        "Auth enforced": has("Auth enforced"),
        "/v1/models metadata": has("/v1/models metadata"),
        "Tool calling (native API)": has("Tool calling (native API)"),
        "Context honesty": ctx_honest,
        "Model quality": quality_ok,
        "Stable under load": stable,
        f"TTFT p95 ≤ {sla:g}s @ knee": ttft_ok,
        f"TTFT p99 ≤ {2 * sla:g}s @ knee": a["ttft_p99_knee"] <= 2 * sla,
    }
    # Token-count inflation is billing fraud → a hard block for a paying router.
    or_crit = has("Streaming (SSE)") and has("Usage accounting") and stable and tok_honest
    or_frac = sum(1 for v in or_gates.values() if v) / len(or_gates)

    # HuggingFace / TGI: throughput-serving (batching-scaling), and HF's provider
    # validation runs a documented TTFT < 5 s check plus tool-calling and
    # structured-output behavioural tests on LLMs.
    hf_gates = {
        "Streaming (SSE)": has("Streaming (SSE)"),
        "Chat endpoint": has("Chat endpoint"),
        "max_tokens honored": has("max_tokens honored"),
        "Concurrent requests": has("Concurrent requests"),
        "/v1/models metadata": has("/v1/models metadata"),
        "TTFT < 5s (HF)": a["ttft_p95_base"] <= 5.0,
        "Tool calling (native API)": has("Tool calling (native API)"),
        "Structured output": has("Structured output"),
        "Context honesty": ctx_honest,
        "Model quality": quality_ok,
        "Stable under load": stable,
        "Batching scales (≥1.5×)": a["scale"] >= 1.5,
    }
    hf_crit = has("Streaming (SSE)") and has("Concurrent requests") and stable
    hf_frac = sum(1 for v in hf_gates.values() if v) / len(hf_gates)

    return {
        "openrouter": {"score": or_frac, "gates": or_gates,
                       "verdict": _rd_verdict(or_frac, or_crit, or_gates,
                                              "voog / usage-arvestus / token-ausus / stabiilsus puudu")},
        "huggingface": {"score": hf_frac, "gates": hf_gates,
                        "verdict": _rd_verdict(hf_frac, hf_crit, hf_gates,
                                               "voog / concurrency / stabiilsus puudu")},
    }


async def provider_readiness(client: LLMClient, model: Optional[str], *,
                             in_tokens: int = 1024, out_tokens: int = 256,
                             sweep_levels: tuple = (1, 4, 8, 16, 32),
                             reqs_per_level: int = 16, ttft_sla_s: float = 3.0,
                             overload: bool = True, distinct_prefix: bool = True,
                             ctx_probe_tokens: int = 8192, integrity: bool = True,
                             on_progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Grade a backend's fitness for OpenRouter / HuggingFace traffic and locate
    its first bottleneck. Streams progress via `on_progress(event)` and returns a
    report with `compliance`, `sweep`, `analysis` and per-provider `verdicts`.
    Naturally cancellable — cancelling the task raises at the next await."""
    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")

    def emit(evt: dict):
        if on_progress:
            on_progress(evt)

    report: dict = {"model": model, "in_tokens": in_tokens, "out_tokens": out_tokens,
                    "ttft_sla_s": ttft_sla_s}

    # Detect reasoning models up front — their hidden chain-of-thought empties the
    # visible `content` under a small token budget, so the correctness/quality/recall
    # probes must give them room and strip <think> to read the real answer.
    reasoning = await _detect_reasoning(client, model)
    report["reasoning_model"] = reasoning
    if reasoning:
        emit({"event": "note", "text": "Reasoning model detected — probes use an expanded "
              "token budget and strip <think> reasoning to read the visible answer."})

    # Phase 1 — API contract compliance.
    emit({"event": "phase", "name": "compliance", "label": "API contract compliance"})
    report["compliance"] = await _readiness_compliance(client, model, emit, reasoning=reasoning)

    # Phase 2 — integrity / honesty (billing, context, model quality).
    if integrity:
        emit({"event": "phase", "name": "integrity", "label": "Integrity — billing / context / quality"})
        report["integrity"] = await _readiness_integrity(
            client, model, emit, ctx_probe_tokens=ctx_probe_tokens, in_tokens=in_tokens,
            reasoning=reasoning)

    # Phase 3 — concurrency sweep / bottleneck hunt.
    emit({"event": "phase", "name": "sweep", "label": "Concurrency sweep — bottleneck hunt"})
    levels = sorted({int(x) for x in sweep_levels if int(x) >= 1})
    rows: list = []
    for lvl in levels:
        reqs = max(reqs_per_level, 2 * lvl)
        stats = await load(client, model, concurrency=lvl, requests=reqs,
                           max_tokens=out_tokens, ctx_tokens=in_tokens,
                           distinct_prefix=distinct_prefix, force_output=True)
        row = _readiness_row(lvl, stats, overload=False)
        rows.append(row)
        emit({"event": "sweep", "row": row})
    if overload and levels:
        ov = max(levels[-1] + 1, round(levels[-1] * 1.25))
        stats = await load(client, model, concurrency=ov, requests=max(reqs_per_level, 2 * ov),
                           max_tokens=out_tokens, ctx_tokens=in_tokens,
                           distinct_prefix=distinct_prefix, force_output=True)
        row = _readiness_row(ov, stats, overload=True)
        rows.append(row)
        emit({"event": "sweep", "row": row})

    report["sweep"] = rows
    report["analysis"] = _readiness_analysis(rows)
    report["verdicts"] = _readiness_verdicts(report)
    emit({"event": "done", "report": report})
    return report


# ---------------------------------------------------------------------------
# Capability discovery
#
# Probes what an endpoint/model actually offers: which API routes are served
# (embeddings, rerank, tokenize, audio, images, …) and which chat features work
# (streaming, tool-calling, JSON mode, vision, logprobs, seed, …). Each probe is
# one small request and yields a row: supported ("yes") / not ("no") / present
# but unverified ("maybe") / couldn't reach ("error") / not applicable ("na").
# ---------------------------------------------------------------------------

# A 1×1 transparent PNG, for the vision (image input) probe.
_TINY_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAA"
             "C1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# Substrings that flag a model id as (likely) an embedding model — tried first
# when sweeping a multi-model router for embedding support.
_EMBED_HINTS = ("embed", "bge", "e5", "gte", "nomic", "minilm", "mxbai", "arctic",
                "jina", "instructor", "sfr", "stella", "granite-embedding")


def _cap_row(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


async def capabilities_probe(client: LLMClient, model: Optional[str], *,
                             on_progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Discover which endpoints and chat features this server/model supports.

    Sends a battery of small probes across three groups — API endpoints, chat
    features, and model metadata — and returns a report with per-capability
    status. Streams each row via `on_progress` ({"event":"item"|"group"|"done"})
    so a UI can fill in live. Cancellable at any await.
    """
    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")

    emit = on_progress or (lambda *_: None)
    groups: list[dict] = []

    def start(group: str):
        g = {"group": group, "items": []}
        groups.append(g)
        emit({"event": "group", "group": group})
        return g

    def add(g, row):
        g["items"].append(row)
        emit({"event": "item", "group": g["group"], "item": row})
        return row

    # ---- Group 1: API endpoints ---------------------------------------------
    g_api = start("API endpoints")

    # /v1/models — always the first thing a router calls.
    raw = await client.list_models_raw()
    add(g_api, _cap_row("Model listing (/v1/models)", "yes" if raw else "no",
                        f"{len(raw)} model(s) advertised" if raw else "no /v1/models data"))

    # /health — a liveness route most routers/vLLM expose (usually an empty 200).
    rh = await client.probe_json("GET", "/health")
    add(g_api, _cap_row("Health (/health)",
                        "yes" if rh["status"] == 200 else ("no" if not rh["present"] else "maybe"),
                        _http_detail(rh)))

    # Chat + text completion routes.
    rc = await client.probe_json("POST", "/v1/chat/completions",
                                 {"model": model, "messages": [{"role": "user", "content": "hi"}],
                                  "max_tokens": 1, "stream": False})
    add(g_api, _cap_row("Chat completions (/v1/chat/completions)",
                        "yes" if rc["status"] == 200 else ("no" if not rc["present"] else "maybe"),
                        _http_detail(rc)))
    rt = await client.probe_json("POST", "/v1/completions",
                                 {"model": model, "prompt": "hi", "max_tokens": 1, "stream": False})
    add(g_api, _cap_row("Text completions (/v1/completions)",
                        "yes" if rt["status"] == 200 else ("no" if not rt["present"] else "maybe"),
                        _http_detail(rt)))

    # Embeddings — the capability the user specifically wanted. On a multi-model
    # router the selected (chat) model usually can't embed, but the same
    # /v1/embeddings route may serve a dedicated embedding model, so if the
    # selected model fails we sweep the OTHER advertised models (embedding-looking
    # names first) to find one that does — answering "does this router do
    # embeddings at all, and with which model" in a single scan.
    async def _try_embed(m):
        rp = await client.probe_json("POST", "/v1/embeddings", {"model": m, "input": "hello world"})
        data = (rp["json"] or {}).get("data") if rp["status"] == 200 else None
        d = (len(data[0]["embedding"]) if data and isinstance(data, list)
             and isinstance(data[0], dict) and isinstance(data[0].get("embedding"), list) else None)
        return rp, d

    re_, dim = await _try_embed(model)
    if dim:
        add(g_api, _cap_row("Embeddings (/v1/embeddings)", "yes", f"vector dim {dim}"))
    else:
        others = [m.get("id") for m in raw if m.get("id") and m.get("id") != model]
        others.sort(key=lambda mid: 0 if any(h in mid.lower() for h in _EMBED_HINTS) else 1)
        cap = 12
        present_any = re_["present"]
        found, tried = None, 0
        if others:
            emit({"event": "status",
                  "text": f"embeddings: selected model can't embed — trying {min(len(others), cap)} "
                          f"other model(s)…"})
        for mid in others[:cap]:
            rp, d = await _try_embed(mid)
            tried += 1
            present_any = present_any or rp["present"]
            if d:
                found = (mid, d)
                break
        if found:
            add(g_api, _cap_row("Embeddings (/v1/embeddings)", "yes",
                                f"via model ‘{found[0]}’ — vector dim {found[1]}"))
        elif not present_any:
            add(g_api, _cap_row("Embeddings (/v1/embeddings)", "no", "no /v1/embeddings route"))
        elif tried:
            more = f" (checked {tried} of {len(others)})" if len(others) > cap else ""
            add(g_api, _cap_row("Embeddings (/v1/embeddings)", "no",
                                f"route present, but no embedding model found{more} — "
                                f"selected model and {tried} other(s) can't embed"))
        else:
            add(g_api, _cap_row("Embeddings (/v1/embeddings)", "no",
                                f"route present, this model can't embed — {_http_detail(re_)}"))

    # Reranking — /v1/rerank (vLLM/Infinity/Jina) or bare /rerank (TEI).
    rr = await client.probe_json("POST", "/v1/rerank",
                                 {"model": model, "query": "fruit",
                                  "documents": ["an apple", "a car"]})
    if not rr["present"]:
        rr = await client.probe_json("POST", "/rerank",
                                     {"model": model, "query": "fruit",
                                      "documents": ["an apple", "a car"]})
    rr_ok = rr["status"] == 200 and bool((rr["json"] or {}).get("results")
                                         or (rr["json"] or {}).get("data"))
    add(g_api, _cap_row("Reranking (/v1/rerank)",
                        "yes" if rr_ok else ("no" if not rr["present"] else "maybe"),
                        _http_detail(rr)))

    # Tokenize — vLLM exposes /tokenize (no /v1 prefix).
    tk = await client.probe_json("POST", "/tokenize", {"model": model, "prompt": "hello world"})
    tk_ct = (tk["json"] or {}).get("count") if tk["status"] == 200 else None
    if tk["status"] == 200 and (tk_ct is not None or (tk["json"] or {}).get("tokens")):
        n = tk_ct if tk_ct is not None else len((tk["json"] or {}).get("tokens") or [])
        add(g_api, _cap_row("Tokenize (/tokenize)", "yes", f"{n} tokens for a 2-word prompt"))
    else:
        add(g_api, _cap_row("Tokenize (/tokenize)", "no" if not tk["present"] else "maybe",
                            _http_detail(tk)))

    # Moderations / images / audio — route-presence probes (a non-404 response,
    # even a validation error, means the route is served by this gateway).
    for name, path, body in (
        ("Moderations (/v1/moderations)", "/v1/moderations", {"model": model, "input": "hello"}),
        ("Image generation (/v1/images/generations)", "/v1/images/generations",
         {"model": model, "prompt": "a red cube", "n": 1}),
        ("Text-to-speech (/v1/audio/speech)", "/v1/audio/speech",
         {"model": model, "input": "hello", "voice": "alloy"}),
        ("Speech-to-text (/v1/audio/transcriptions)", "/v1/audio/transcriptions", {}),
    ):
        rp = await client.probe_json("POST", path, body)
        if rp["status"] == 200:
            st = "yes"
        elif rp["present"]:
            st = "maybe"
        else:
            st = "no"
        detail = ("route present" if st == "maybe" else _http_detail(rp))
        add(g_api, _cap_row(name, st, detail))

    # ---- Group 2: chat features ---------------------------------------------
    g_feat = start("Chat features")
    if client.endpoint == "completions":
        add(g_feat, _cap_row("(chat features)", "na",
                             "the active endpoint is /v1/completions — switch to chat to probe these"))
    elif rc["status"] != 200:
        add(g_feat, _cap_row("(chat features)", "na",
                             "chat completions not available on this endpoint"))
    else:
        await _probe_chat_features(client, model, g_feat, add)

    # ---- Group 3: model metadata --------------------------------------------
    g_meta = start("Model metadata")
    entry = None
    for m in raw:
        if m.get("id") == model:
            entry = m
            break
    entry = entry or (raw[0] if raw else {})
    ctx = None
    for k in ("max_model_len", "max_context_length", "context_length",
              "max_seq_len", "max_position_embeddings"):
        if isinstance(entry.get(k), int) and entry[k] > 0:
            ctx = entry[k]
            break
    add(g_meta, _cap_row("Advertised context length", "yes" if ctx else "no",
                         f"{ctx:,} tokens" if ctx else "not exposed in /v1/models"))
    pricing = entry.get("pricing")
    add(g_meta, _cap_row("Pricing metadata", "yes" if pricing else "no",
                         _pricing_detail(pricing) if pricing else "not exposed"))
    owner = entry.get("owned_by") or entry.get("owner")
    add(g_meta, _cap_row("Owner / provider", "yes" if owner else "no", str(owner) if owner else "—"))
    add(g_meta, _cap_row("Models on server", "yes" if raw else "no",
                         f"{len(raw)} model(s)" if raw else "—"))

    supported = sum(1 for g in groups for it in g["items"] if it["status"] == "yes")
    total = sum(1 for g in groups for it in g["items"] if it["status"] in ("yes", "no", "maybe"))
    report = {"model": model, "endpoint": client.endpoint, "groups": groups,
              "supported": supported, "total": total}
    emit({"event": "done", "report": report})
    return report


def _http_detail(rp: dict) -> str:
    """A compact human detail for a probe response."""
    if rp["status"] == 200:
        return "OK (200)"
    if rp["status"] == 0:
        return rp.get("error") or "no response"
    msg = rp.get("error") or ""
    msg = " ".join(msg.split())[:80]
    return f"HTTP {rp['status']}" + (f" — {msg}" if msg else "")


def _pricing_detail(pricing) -> str:
    if isinstance(pricing, dict):
        p = pricing.get("prompt") or pricing.get("input")
        c = pricing.get("completion") or pricing.get("output")
        if p is not None or c is not None:
            return f"in {p} / out {c}"
    return "present"


async def _probe_chat_features(client: LLMClient, model: str, g, add) -> None:
    """Probe the individual chat-completion features and add a row for each."""
    # Streaming (SSE): a real token-by-token stream shows multiple chunks.
    r = await client.generate(model=model, prompt="Count from 1 to 8.",
                              max_tokens=16, retries=_PROBE_RETRIES)
    if r.ok and r.stream_chunks >= 2:
        add(g, _cap_row("Streaming (SSE)", "yes", f"{r.stream_chunks} streamed chunks"))
    elif r.ok:
        add(g, _cap_row("Streaming (SSE)", "maybe", "server buffered (1 chunk) — no token stream"))
    else:
        add(g, _cap_row("Streaming (SSE)", "error", r.error[:80]))

    # Native tool / function calling.
    rt = await client.generate(model=model, prompt="What's the weather in Tallinn? Use celsius.",
                               max_tokens=128, temperature=0.0,
                               tools=[_NATIVE_TOOL_SCHEMA], tool_choice="auto",
                               retries=_PROBE_RETRIES)
    if rt.ok and rt.tool_calls and rt.tool_calls[0][0] == "get_weather":
        add(g, _cap_row("Tool / function calling (native)", "yes",
                        f"called {rt.tool_calls[0][0]}(…)"))
    elif not rt.ok:
        add(g, _cap_row("Tool / function calling (native)", "no", _http_short(rt.error)))
    elif rt.tool_calls:
        add(g, _cap_row("Tool / function calling (native)", "maybe",
                        f"called {rt.tool_calls[0][0]} (unexpected tool)"))
    else:
        add(g, _cap_row("Tool / function calling (native)", "no", "no tool_calls returned"))

    # JSON object mode — the prompt mentions JSON (some servers require it) and the
    # server must return valid JSON for a "yes".
    rj = await client.probe_json("POST", "/v1/chat/completions", {
        "model": model, "max_tokens": 80, "temperature": 0.0, "stream": False,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user",
                      "content": "Reply with a JSON object giving the capital of Estonia."}]})
    add(g, _json_probe_row("JSON object mode (response_format)", rj))

    # JSON schema mode (structured outputs) — a NATURAL prompt plus a required-keys
    # check, so a "yes" means the server actually enforced the schema rather than
    # silently ignoring response_format and returning prose.
    schema = {"type": "object", "properties": {"city": {"type": "string"},
              "population": {"type": "integer"}}, "required": ["city", "population"]}
    rs = await client.probe_json("POST", "/v1/chat/completions", {
        "model": model, "max_tokens": 80, "temperature": 0.0, "stream": False,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "city_info", "schema": schema}},
        "messages": [{"role": "user", "content": "Tell me about the city of Tallinn."}]})
    add(g, _json_probe_row("JSON schema mode (structured outputs)", rs,
                           required=("city", "population")))

    # Vision (image input). A text-only server rejects image content with a 400
    # ("content must be a string"), so accepting the message implies multimodal.
    rv = await client.probe_json("POST", "/v1/chat/completions", {
        "model": model, "max_tokens": 16, "temperature": 0.0, "stream": False,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What colour is this pixel?"},
            {"type": "image_url", "image_url": {"url": _TINY_PNG}}]}]})
    if rv["status"] == 200:
        add(g, _cap_row("Vision (image input)", "yes", "accepted image input"))
    elif not rv["present"]:
        add(g, _cap_row("Vision (image input)", "no", "chat route not found"))
    else:
        add(g, _cap_row("Vision (image input)", "no", f"rejected — {_http_detail(rv)}"))

    # Multiple choices (n > 1).
    rn = await client.probe_json("POST", "/v1/chat/completions", {
        "model": model, "max_tokens": 8, "temperature": 1.0, "n": 2, "stream": False,
        "messages": [{"role": "user", "content": "Say a random word."}]})
    nch = len((rn["json"] or {}).get("choices") or []) if rn["status"] == 200 else 0
    add(g, _cap_row("Multiple choices (n>1)", "yes" if nch >= 2 else "no",
                    f"returned {nch} choices" if rn["status"] == 200 else _http_detail(rn)))

    # logprobs.
    rl = await client.generate(model=model, prompt="Say: ok.", max_tokens=4,
                               logprobs=True, retries=_PROBE_RETRIES)
    add(g, _cap_row("Token logprobs", "yes" if rl.logprob_avg is not None else "no",
                    f"avg logprob {rl.logprob_avg:.2f}" if rl.logprob_avg is not None
                    else "no logprobs in response"))

    # Stop sequences.
    rstop = await client.generate(model=model, prompt="Recite the digits: 1 2 3 4 5 6 7 8 9",
                                  max_tokens=40, temperature=0.0, stop=["5"],
                                  retries=_PROBE_RETRIES)
    if rstop.ok and rstop.finish_reason == "stop" and "6" not in (rstop.text or ""):
        add(g, _cap_row("Stop sequences", "yes", "honoured the stop token"))
    elif rstop.ok:
        add(g, _cap_row("Stop sequences", "maybe", "accepted but not clearly honoured"))
    else:
        add(g, _cap_row("Stop sequences", "no", _http_short(rstop.error)))

    # Reproducible sampling (seed): same seed at temperature>0 → identical output.
    a = await client.generate(model=model, prompt="Write one random sentence.",
                              max_tokens=24, temperature=1.0, seed=12345, retries=_PROBE_RETRIES)
    b = await client.generate(model=model, prompt="Write one random sentence.",
                              max_tokens=24, temperature=1.0, seed=12345)
    if a.ok and b.ok and a.text and a.text == b.text:
        add(g, _cap_row("Reproducible sampling (seed)", "yes", "identical output for a fixed seed"))
    elif a.ok and b.ok:
        add(g, _cap_row("Reproducible sampling (seed)", "no", "seed did not fix the output"))
    else:
        add(g, _cap_row("Reproducible sampling (seed)", "error", _http_short(a.error or b.error)))

    # Reasoning / thinking model.
    is_reasoning = await _detect_reasoning(client, model)
    add(g, _cap_row("Reasoning / thinking output", "yes" if is_reasoning else "no",
                    "emits chain-of-thought (reasoning model)" if is_reasoning
                    else "standard model (no separate reasoning)"))


def _http_short(err: str) -> str:
    return " ".join((err or "").split())[:80] or "request failed"


def _json_probe_row(name: str, rp: dict, required=None) -> dict:
    """Judge a response_format probe. 200 + valid JSON (and, if `required` is
    given, containing those keys) → the server enforced structured output ("yes").
    A 200 with prose (field silently ignored) or missing keys → "no"; a 4xx that
    rejected the field → "no" with the server's reason."""
    if rp["status"] != 200:
        return _cap_row(name, "no", _http_detail(rp))
    try:
        content = (((rp["json"] or {}).get("choices") or [{}])[0]
                   .get("message", {}).get("content") or "")
        obj = json.loads(content)
    except Exception:
        return _cap_row(name, "no", "response_format not enforced — output wasn't valid JSON")
    if required and not (isinstance(obj, dict) and all(k in obj for k in required)):
        return _cap_row(name, "no", "returned JSON but it didn't match the requested schema")
    return _cap_row(name, "yes",
                    "enforced the schema" if required else "returned valid JSON")


# ---------------------------------------------------------------------------
# Embedding speed test
#
# Throughput + latency for an /v1/embeddings model: hold `concurrency` batch
# requests in flight for a window and measure embeddings/s, input tokens/s, and
# per-request latency. Batching matters a lot for embedding servers, so batch
# size is a first-class knob.
# ---------------------------------------------------------------------------

async def embed_speed_test(client: LLMClient, model: Optional[str], *,
                           batch_size: int = 32, concurrency: int = 8,
                           input_tokens: int = 64, duration_s: float = 15.0,
                           distinct: bool = True, report_interval: float = 2.0,
                           on_progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Measure embedding throughput and latency under sustained load.

    `concurrency` workers each POST a batch of `batch_size` texts (~`input_tokens`
    tokens each) to /v1/embeddings back-to-back for `duration_s` seconds. Reports
    embeddings/s (texts), input tokens/s, requests/s, latency p50/p95 and the
    vector dimension. A preflight embed validates the model first (so picking a
    chat model fails fast with a clear message). Emits a snapshot every
    `report_interval`s via `on_progress`. Cancellable at any await.
    """
    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")

    # Preflight: confirm this model actually embeds, and capture the dimension.
    pf = await client.embed(model=model, inputs=["hello world"])
    if not pf["ok"]:
        raise RuntimeError(f"/v1/embeddings failed for model '{model}': {pf['error']}")
    dim0 = pf["dim"]

    # Estimated tokens per text — used for tokens/s when the server omits usage.
    est_per_text = max(1, approx_tokens(_filler(input_tokens)))

    def make_text() -> str:
        salt = (_unique_prefix(24) + " ") if distinct else ""
        return salt + _filler(input_tokens, lead=random.randint(0, 10 ** 9))

    start = time.perf_counter()
    deadline = start + max(1.0, duration_s)
    # (t_rel, ok, n_texts, tokens_reported, latency, dim, est, err)
    recs: list[tuple] = []

    async def worker():
        while time.perf_counter() < deadline:
            batch = [make_text() for _ in range(batch_size)]
            r = await client.embed(model=model, inputs=batch)
            est = r["tokens"] == 0
            toks = r["tokens"] if r["tokens"] else est_per_text * batch_size
            recs.append((time.perf_counter() - start, r["ok"], r["n"], toks,
                         r["latency"], r["dim"], est, "" if r["ok"] else r["error"]))
            if not r["ok"]:
                await asyncio.sleep(0.5)

    def snapshot() -> dict:
        el = max(time.perf_counter() - start, 1e-9)
        ok = [x for x in recs if x[1]]
        errs = [x[7] for x in recs if not x[1]]
        embs = sum(x[2] for x in ok)
        toks = sum(x[3] for x in ok)
        lat = [x[4] for x in ok]
        dim = next((x[5] for x in reversed(ok) if x[5]), dim0)
        est_frac = (sum(1 for x in ok if x[6]) / len(ok)) if ok else 0.0
        # windowed embeddings/s series for the chart
        buckets: dict = {}
        for x in ok:
            b = int(x[0] // report_interval)
            buckets[b] = buckets.get(b, 0) + x[2]
        series = [(f"{int(b * report_interval)}s", cnt / report_interval)
                  for b, cnt in sorted(buckets.items())]
        return {
            "elapsed": el, "remaining": max(0.0, deadline - time.perf_counter()),
            "duration": duration_s, "concurrency": concurrency, "batch_size": batch_size,
            "requests": len(recs), "success": len(ok), "errors": len(errs),
            "embeddings": embs, "tokens": toks, "dim": dim,
            "emb_per_s": embs / el, "tok_per_s": toks / el, "req_per_s": len(ok) / el,
            "lat_p50": _pct(lat, 0.5), "lat_p95": _pct(lat, 0.95),
            "lat_mean": (sum(lat) / len(lat)) if lat else 0.0,
            "ms_per_emb": (1000.0 * sum(lat) / embs) if embs else 0.0,
            "est_frac": est_frac, "error_samples": errs[:3], "series": series,
            "model": model, "input_tokens": input_tokens,
        }

    async def reporter():
        while time.perf_counter() < deadline:
            await asyncio.sleep(report_interval)
            if on_progress:
                on_progress(snapshot())

    workers = [asyncio.create_task(worker()) for _ in range(max(1, concurrency))]
    rep = asyncio.create_task(reporter())
    try:
        await asyncio.gather(*workers)
    finally:
        rep.cancel()
    return snapshot()


# ---------------------------------------------------------------------------
# Embedding quality test
#
# Beyond speed: does the model produce USEFUL embeddings (retrieval works,
# paraphrases cluster, cross-lingual pairs align), are the vectors well-formed
# (L2-normalised, deterministic), and what are its limits (max input tokens,
# max batch)? Plus an optional reranker relevance check. Each probe → a row with
# pass/fail and the measured numbers, grouped like the capability scan.
# ---------------------------------------------------------------------------

_RETR_QUERY = "How often should I feed my cat?"
_RETR_DOCS = [
    "Most adult cats should be fed twice a day with a balanced diet.",   # relevant (0)
    "The central bank raised its benchmark interest rate this week.",
    "Mount Everest is the highest mountain above sea level on Earth.",
    "To build the project, run the compile script from your terminal.",
]
_PARA_A = "The cat sat quietly on the warm windowsill in the sun."
_PARA_SIM = "A cat rested calmly on the sunny window ledge."
_PARA_UNREL = "The company reported record quarterly revenue this year."
# Estonian sentence, its English translation, and an unrelated English sentence.
_ML_ET = "Kass istub vaikselt päikesepaistelisel aknalaual."
_ML_EN = "The cat sits quietly on the sunny windowsill."
_ML_EN_UNREL = "Global stock markets fell sharply this morning."

_RERANK_QUERY = "What is the capital of Estonia?"
_RERANK_DOCS = [
    "Berlin is the capital and largest city of Germany.",
    "Tallinn is the capital and most populous city of Estonia.",   # relevant (1)
    "The Baltic Sea borders several countries in northern Europe.",
]
# Model-id substrings that flag a dedicated reranker (tried when sweeping).
_RERANK_HINTS = ("rerank", "reranker", "ce-", "cross-encoder", "colbert")


def _cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _l2(v) -> float:
    return math.sqrt(sum(x * x for x in v)) if v else 0.0


async def embed_quality_test(client: LLMClient, model: Optional[str], *,
                             on_progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Assess embedding QUALITY (not speed): retrieval, similarity structure,
    cross-lingual alignment, vector properties (L2-norm, determinism, dim), input
    /batch limits, and an optional reranker relevance check. Streams rows via
    `on_progress` ({"event":"group"|"item"|"status"|"done"}) like the capability
    scan. A preflight embed validates the model first."""
    if not model:
        try:
            models = await client.list_models()
            model = models[0] if models else None
        except Exception:
            model = None
        if not model:
            raise RuntimeError("No model specified and model listing failed.")

    pf = await client.embed(model=model, inputs=["hello world"], keep_vectors=True)
    if not pf["ok"]:
        raise RuntimeError(f"/v1/embeddings failed for model '{model}': {pf['error']}")
    dim0 = pf["dim"]

    emit = on_progress or (lambda *_: None)
    groups: list[dict] = []
    raw = await client.list_models_raw()

    def start(name):
        g = {"group": name, "items": []}
        groups.append(g)
        emit({"event": "group", "group": name})
        return g

    def add(g, name, status, detail=""):
        row = {"name": name, "status": status, "detail": detail}
        g["items"].append(row)
        emit({"event": "item", "group": g["group"], "item": row})
        return row

    async def vecs(texts):
        r = await client.embed(model=model, inputs=texts, keep_vectors=True)
        return r.get("vectors") if r["ok"] else None

    # ---- Group 1: retrieval & similarity ------------------------------------
    g_ret = start("Retrieval & similarity")

    # Retrieval sanity: the relevant doc must rank first by cosine to the query.
    rv = await vecs([_RETR_QUERY] + _RETR_DOCS)
    if not rv or len(rv) != len(_RETR_DOCS) + 1:
        add(g_ret, "Retrieval ranking", "error", "embedding call failed")
    else:
        q, docs = rv[0], rv[1:]
        sims = [_cosine(q, d) for d in docs]
        best = max(range(len(sims)), key=lambda i: sims[i])
        second = sorted(sims, reverse=True)[1]
        ok = best == 0 and (sims[0] - second) > 0.02
        add(g_ret, "Retrieval ranking", "yes" if ok else "no",
            f"relevant doc sim {sims[0]:.2f} vs best-distractor {second:.2f}"
            + ("" if ok else f" — ranked #{best + 1} instead"))

    # Paraphrase clusters, unrelated separates.
    pv = await vecs([_PARA_A, _PARA_SIM, _PARA_UNREL])
    if not pv or len(pv) != 3:
        add(g_ret, "Paraphrase vs unrelated", "error", "embedding call failed")
    else:
        s_par = _cosine(pv[0], pv[1])
        s_unrel = _cosine(pv[0], pv[2])
        ok = s_par > s_unrel + 0.1
        add(g_ret, "Paraphrase vs unrelated", "yes" if ok else "no",
            f"paraphrase sim {s_par:.2f} vs unrelated {s_unrel:.2f}"
            + ("" if ok else " — too close, weak semantic separation"))

    # Cross-lingual: Estonian sentence should align with its English translation
    # more than with an unrelated English sentence.
    mv = await vecs([_ML_ET, _ML_EN, _ML_EN_UNREL])
    if not mv or len(mv) != 3:
        add(g_ret, "Multilingual (Estonian ↔ English)", "error", "embedding call failed")
    else:
        s_tr = _cosine(mv[0], mv[1])
        s_un = _cosine(mv[0], mv[2])
        ok = s_tr > s_un + 0.05 and s_tr > 0.4
        status = "yes" if ok else ("maybe" if s_tr > s_un else "no")
        add(g_ret, "Multilingual (Estonian ↔ English)", status,
            f"ET↔EN translation sim {s_tr:.2f} vs unrelated {s_un:.2f}"
            + ("" if ok else " — likely English-only / weak cross-lingual"))

    # ---- Group 2: vector properties -----------------------------------------
    g_prop = start("Vector properties")

    nv = await vecs([_PARA_A])
    if nv and nv[0]:
        norm = _l2(nv[0])
        ok = 0.97 <= norm <= 1.03
        add(g_prop, "L2 normalised (‖v‖ ≈ 1)", "yes" if ok else "no",
            f"‖v‖ = {norm:.3f}" + ("" if ok else " — normalise before cosine/dot search"))
    else:
        add(g_prop, "L2 normalised (‖v‖ ≈ 1)", "error", "embedding call failed")

    a1 = await vecs([_PARA_A])
    a2 = await vecs([_PARA_A])
    if a1 and a2 and a1[0] and a2[0]:
        maxdiff = max(abs(x - y) for x, y in zip(a1[0], a2[0]))
        ok = maxdiff < 1e-5
        add(g_prop, "Deterministic (repeatable vectors)",
            "yes" if ok else ("maybe" if maxdiff < 1e-3 else "no"),
            f"max element delta {maxdiff:.1e} across two calls")
    else:
        add(g_prop, "Deterministic (repeatable vectors)", "error", "embedding call failed")

    add(g_prop, "Vector dimension", "yes" if dim0 else "no",
        f"{dim0} floats" if dim0 else "unknown")

    # ---- Group 3: limits ----------------------------------------------------
    g_lim = start("Limits")
    emit({"event": "status", "text": "probing max input length…"})
    max_ok, trunc_at = 0, None
    for size in (512, 2048, 8192):
        r = await client.embed(model=model, inputs=[_filler(size)])
        if not r["ok"]:
            break
        max_ok = size
        sent = approx_tokens(_filler(size))
        if r["tokens"] and r["tokens"] < sent * 0.7:
            trunc_at = r["tokens"]
            break
    if trunc_at:
        add(g_lim, "Max input length", "yes", f"silently truncates at ~{trunc_at} tokens")
    elif max_ok:
        add(g_lim, "Max input length", "yes",
            f"handled ~{max_ok} tokens" + (" (largest tested)" if max_ok >= 8192 else "; errors above"))
    else:
        add(g_lim, "Max input length", "no", "failed even at ~512 tokens")

    emit({"event": "status", "text": "probing max batch size…"})
    max_batch = 0
    for bs in (1, 32, 256, 1024):
        r = await client.embed(model=model, inputs=["hello world"] * bs)
        if r["ok"] and r["n"] >= bs:
            max_batch = bs
        else:
            break
    add(g_lim, "Max batch size", "yes" if max_batch else "no",
        (f"≥{max_batch} inputs/request (largest tested)" if max_batch >= 1024
         else f"~{max_batch} inputs/request" if max_batch else "batch of 1 failed"))

    # ---- Group 4: reranking (only if /v1/rerank is served) ------------------
    rr = await client.probe_json("POST", "/v1/rerank",
                                 {"model": model, "query": _RERANK_QUERY,
                                  "documents": _RERANK_DOCS})
    rerank_path = "/v1/rerank"
    if not rr["present"]:
        rr2 = await client.probe_json("POST", "/rerank",
                                      {"model": model, "query": _RERANK_QUERY,
                                       "documents": _RERANK_DOCS})
        if rr2["present"]:
            rr, rerank_path = rr2, "/rerank"
    if rr["present"]:
        g_rr = start("Reranking")
        rmodel, res = model, rr
        if res["status"] != 200 or _rerank_top_index(res) is None:
            # The embedding model probably isn't a reranker — sweep for one. Adopt
            # the first model that returns a usable 200 (so we can judge its
            # ranking) and stop early if one also ranks correctly.
            cands = [m.get("id") for m in raw
                     if m.get("id") and m.get("id") != model
                     and any(h in m.get("id", "").lower() for h in _RERANK_HINTS)]
            if cands:
                emit({"event": "status", "text": f"trying {len(cands)} reranker model(s)…"})
            for mid in cands[:6]:
                cand = await client.probe_json("POST", rerank_path,
                                               {"model": mid, "query": _RERANK_QUERY,
                                                "documents": _RERANK_DOCS})
                if cand["status"] == 200 and _rerank_top_index(cand) is not None:
                    rmodel, res = mid, cand
                    if _rerank_top_ok(cand):
                        break
        top = _rerank_top_index(res)
        if res["status"] == 200 and top is not None:
            ok = top == 1  # _RERANK_DOCS[1] is the relevant one
            via = "" if rmodel == model else f" (via ‘{rmodel}’)"
            add(g_rr, "Relevance ranking", "yes" if ok else "no",
                (f"ranked the relevant doc #1{via}" if ok
                 else f"ranked doc #{top + 1} first, not the relevant one{via}"))
        else:
            add(g_rr, "Relevance ranking", "no",
                f"no usable rerank result — {_http_detail(res)}")

    supported = sum(1 for g in groups for it in g["items"] if it["status"] == "yes")
    total = sum(1 for g in groups for it in g["items"] if it["status"] in ("yes", "no", "maybe"))
    report = {"model": model, "dim": dim0, "groups": groups,
              "supported": supported, "total": total}
    emit({"event": "done", "report": report})
    return report


def _rerank_results(res: dict) -> list:
    """Normalise a rerank response to (index, score) pairs across the common
    shapes (OpenAI-style {results:[{index,relevance_score}]}, TEI [{index,score}])."""
    obj = res.get("json")
    if isinstance(obj, dict):
        items = obj.get("results") or obj.get("data") or []
    elif isinstance(obj, list):
        items = obj
    else:
        items = []
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("index") is not None:
            out.append((int(it["index"]),
                        it.get("relevance_score", it.get("score", 0.0))))
    return out


def _rerank_top_index(res: dict):
    """The document index the reranker scored highest, or None."""
    if res.get("status") != 200:
        return None
    items = _rerank_results(res)
    if not items:
        return None
    return max(items, key=lambda t: t[1])[0]


def _rerank_top_ok(res: dict) -> bool:
    return _rerank_top_index(res) == 1
