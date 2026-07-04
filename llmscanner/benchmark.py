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

# A prompt that strongly encourages the model to keep generating, so that
# throughput numbers reflect real decode speed (not an early EOS).
LONG_PROMPT = (
    "Write an extremely long and detailed technical essay about the history, "
    "architecture and internals of modern operating systems: schedulers, virtual "
    "memory, file systems, and networking. Be verbose and keep writing, do not stop."
)

SANITY_PROMPT = "What is 17 + 25? Give the final answer as a number."

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
        latency_p50=_pct(lats, 0.5),
        latency_p95=_pct(lats, 0.95),
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
                      depths=(0.1, 0.5, 0.9)):
    """Hide a secret code in a long context at several depths and ask for it back."""
    results = []
    total = max(1, int(ctx_tokens * 0.9))
    for d in depths:
        code = f"{random.randint(1000, 9999)}-{random.choice(['BLUE', 'RED', 'GOLD', 'JADE'])}-{random.randint(10, 99)}"
        needle = f"  The secret access code is {code}.  "
        before = int(total * d)
        prompt = (_filler(before) + needle + _filler(max(1, total - before)) +
                  "\n\nQuestion: What is the secret access code? Answer with the code only.")
        r = await client.generate(model=model, prompt=prompt, max_tokens=32, temperature=0.0)
        got = (r.text or "").strip()
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
    {"user": "What is 2348 multiplied by 19?",
     "tool": "calculator", "args": {"expression": ["2348", "19"]}},
    {"user": "Please compute 145 + 998 - 37.",
     "tool": "calculator", "args": {"expression": ["145", "998", "37"]}},
    {"user": "Send an email to john@example.com with the subject Lunch asking if "
             "he is free at noon tomorrow.",
     "tool": "send_email", "args": {"to": "john@example.com", "subject": "lunch"}},
    {"user": "Hi there! Can you briefly introduce yourself in one sentence?", "tool": None},
    {"user": "Write a short two-line poem about the sea.", "tool": None},
    {"user": "What does the word 'ephemeral' mean?", "tool": None},
]

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

    async def ask(system, user, max_tokens):
        r = await client.generate(model=model, prompt=user, system=system,
                                  max_tokens=max_tokens, temperature=0.0)
        if r.ok and r.total_time > 0:
            lat_samples.append((r.total_time, r.output_tps))
        return r

    report: dict = {"model": model, "dims": list(dims)}

    # ---- Dimension: Hermes tool-calling --------------------------------------
    if "tool" in dims:
        pos = [c for c in _TOOL_CASES if c["tool"]]
        neg = [c for c in _TOOL_CASES if not c["tool"]]
        valid = select = args_ok = false_call = 0
        for c in _TOOL_CASES:
            r = await ask(_HERMES_SYSTEM, c["user"], 512)
            calls = _extract_tool_calls(r.text) if r.ok else []
            if c["tool"]:
                got = calls[0][0] if calls else None
                got_args = calls[0][1] if calls else {}
                v = bool(calls)
                s = v and got == c["tool"]
                a = s and _arg_ok(got_args, c["args"])
                valid += v; select += s; args_ok += a
                ok = a
                detail = (f"→ {got or '∅'}" if not a else f"✓ {got} {got_args}")
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
        return overall, "❌ EI SOBI — ei suuda usaldusväärselt tööriistu kutsuda (Hermes)"
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


async def _readiness_compliance(client: LLMClient, model: str,
                                emit: Callable[[dict], None]) -> list:
    """Run the API-contract probes; return a list of {name, ok, detail, req}."""
    checks: list = []

    def add(name, ok, detail, req):
        row = {"name": name, "ok": bool(ok), "detail": str(detail)[:80], "req": req}
        checks.append(row)
        emit({"event": "check", **row})

    async def one(prompt, max_tokens, **kw):
        return await client.generate(model=model, prompt=prompt, max_tokens=max_tokens, **kw)

    # 1. Basic chat completion.
    r = await one("Reply with the single word: pong.", 16, temperature=0.0)
    add("Chat endpoint", r.ok and bool((r.text or "").strip()),
        (r.text or "").strip()[:40] if r.ok else r.error,
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
                  "else: alpha bravo charlie delta echo", 48, temperature=0.0,
                  stop=["charlie"])
    txt = (r.text or "").lower()
    stop_ok = bool(r.ok and "alpha" in txt and "delta" not in txt and "echo" not in txt)
    add("Stop sequences", stop_ok,
        (repr((r.text or "").strip()[:40]) if r.ok else r.error) if not stop_ok
        else "cut off at the stop token",
        "honors the stop parameter")

    # 7. Determinism at temperature 0 (greedy decode should be reproducible).
    a = await one("Name three colours, comma separated, lowercase.", 24, temperature=0.0)
    b = await one("Name three colours, comma separated, lowercase.", 24, temperature=0.0)
    det = bool(a.ok and b.ok and (a.text or "").strip()[:60] == (b.text or "").strip()[:60])
    add("Deterministic (temp 0)", det,
        "identical output on repeat" if det else "temp-0 output varied between calls",
        "reproducible greedy decoding")

    # 8. Sampling params actually take effect.
    a = await one("Write one short sentence about the sea.", 32, temperature=1.0,
                  top_p=0.95, seed=1)
    b = await one("Write one short sentence about the sea.", 32, temperature=1.0,
                  top_p=0.95, seed=2)
    varies = bool(a.ok and b.ok and (a.text or "").strip() != (b.text or "").strip())
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
                              prompt="hi", max_tokens=8)
    clean = bool((not r.ok) and r.error.startswith("HTTP 4"))
    add("Clean error on bad request", clean,
        (r.error[:60] if not r.ok else "accepted an invalid model — no validation"),
        "returns a 4xx JSON error, not 5xx / timeout")

    emit({"event": "phase_done", "name": "compliance",
          "passed": sum(1 for c in checks if c["ok"]), "total": len(checks)})
    return checks


def _readiness_row(conc: int, stats: "LoadStats", *, overload: bool) -> dict:
    rej, hard = _classify_load_errors(stats.errors)
    req = max(1, stats.requests)
    return {
        "conc": conc, "overload": overload,
        "out_tps": stats.aggregate_tps, "in_tps": stats.input_tps,
        "total_tps": stats.total_tps, "ttft_p95": stats.ttft_p95,
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
        "ttft_p95_knee": knee["ttft_p95"], "scale": scale, "ttft_growth": ttft_growth,
        "tpot_growth": tpot_growth, "hard_frac_max": hard_max,
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

    # OpenRouter: bills on usage, cares about correctness + TTFT + clean backpressure.
    or_gates = {
        "Streaming (SSE)": has("Streaming (SSE)"),
        "Usage accounting": has("Usage accounting"),
        "max_tokens honored": has("max_tokens honored"),
        "Stop sequences": has("Stop sequences"),
        "Concurrent requests": has("Concurrent requests"),
        "Clean errors": has("Clean error on bad request"),
        "Stable under load": stable,
        f"TTFT p95 ≤ {sla:g}s @ knee": ttft_ok,
    }
    or_crit = has("Streaming (SSE)") and has("Usage accounting") and stable
    or_frac = sum(1 for v in or_gates.values() if v) / len(or_gates)

    # HuggingFace / TGI: throughput-serving, so batching-scaling is a first-class gate.
    hf_gates = {
        "Streaming (SSE)": has("Streaming (SSE)"),
        "Chat endpoint": has("Chat endpoint"),
        "max_tokens honored": has("max_tokens honored"),
        "Concurrent requests": has("Concurrent requests"),
        "Stable under load": stable,
        "Batching scales (≥1.5×)": a["scale"] >= 1.5,
    }
    hf_crit = has("Streaming (SSE)") and has("Concurrent requests") and stable
    hf_frac = sum(1 for v in hf_gates.values() if v) / len(hf_gates)

    return {
        "openrouter": {"score": or_frac, "gates": or_gates,
                       "verdict": _rd_verdict(or_frac, or_crit, or_gates,
                                              "voog / usage-arvestus / stabiilsus puudu")},
        "huggingface": {"score": hf_frac, "gates": hf_gates,
                        "verdict": _rd_verdict(hf_frac, hf_crit, hf_gates,
                                               "voog / concurrency / stabiilsus puudu")},
    }


async def provider_readiness(client: LLMClient, model: Optional[str], *,
                             in_tokens: int = 1024, out_tokens: int = 256,
                             sweep_levels: tuple = (1, 4, 8, 16, 32),
                             reqs_per_level: int = 16, ttft_sla_s: float = 3.0,
                             overload: bool = True, distinct_prefix: bool = True,
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

    # Phase 1 — API contract compliance.
    emit({"event": "phase", "name": "compliance", "label": "API contract compliance"})
    report["compliance"] = await _readiness_compliance(client, model, emit)

    # Phase 2 — concurrency sweep / bottleneck hunt.
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
