"""Benchmark routines: latency, throughput, load, context/prefill, sanity."""
from __future__ import annotations

import asyncio
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
