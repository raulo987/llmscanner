"""Optional command-line interface (handy for scripting / SSH sessions).

Requires `rich` (pip install 'llmscanner[cli]'). The GUI does not need it.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics

try:
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover
    raise SystemExit("The CLI needs 'rich'. Install with: pip install rich")

from . import benchmark as B
from .client import LLMClient
from .detect import detect
from .scanner import DEFAULT_PORTS, detect_many, scan_network
from .util import default_subnet, parse_ports

console = Console()


def _table(title, rows):
    t = Table(title=title)
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)


async def cmd_scan(args):
    subnet = args.subnet or default_subnet()
    try:
        ports = parse_ports(args.ports, DEFAULT_PORTS)
    except ValueError as e:
        console.print(f"[red]Invalid --ports:[/red] {e}")
        return 2
    console.print(f"[bold]Scanning[/bold] {subnet} on {len(ports)} ports …")
    pairs = await scan_network(subnet, ports, timeout=args.timeout, concurrency=args.concurrency)
    if not pairs:
        console.print("[yellow]No open ports found.[/yellow]")
        return
    console.print(f"Found [green]{len(pairs)}[/green] open port(s); identifying…")
    servers = await detect_many(pairs, timeout=args.detect_timeout)
    if not servers:
        console.print("[yellow]No LLM servers identified.[/yellow]")
        for h, p in sorted(pairs):
            console.print(f"  {h}:{p}")
        return
    t = Table(title="Discovered LLM servers")
    for c in ("Host", "Port", "Type", "OpenAI API", "Models"):
        t.add_column(c)
    for s in sorted(servers, key=lambda x: (x.host, x.port)):
        models = ", ".join(str(m) for m in s.models[:3]) + ("…" if len(s.models) > 3 else "")
        t.add_row(s.host, str(s.port), s.server_type, "yes" if s.openai_compatible else "no", models)
    console.print(t)


async def cmd_detect(args):
    s = await detect(args.host, args.port, timeout=args.timeout)
    _table(f"{args.host}:{args.port}", [
        ("Reachable", "yes" if s.reachable else "no"),
        ("Server type", s.server_type),
        ("OpenAI compatible", "yes" if s.openai_compatible else "no"),
        ("Version", s.version or "-"),
        ("Models", ", ".join(s.models) or "-"),
    ])


async def cmd_models(args):
    client = LLMClient(args.host, args.port, api_key=args.api_key, timeout=args.timeout)
    models = await client.list_models()
    for m in models:
        console.print(f"  {m}")


async def cmd_bench(args):
    client = LLMClient(args.host, args.port, api_key=args.api_key,
                       timeout=args.timeout, endpoint=args.endpoint)
    model = args.model
    if not model:
        models = await client.list_models()
        if not models:
            console.print("[red]No models available on this server.[/red]")
            return
        model = models[0]
        console.print(f"[dim]Auto-selected model:[/dim] {model}")

    tests = args.test
    if "all" in tests:
        tests = ["sanity", "latency", "throughput", "load", "context"]

    if "sanity" in tests:
        r, passed, exp, got = await B.sanity(client, model)
        _table("Sanity", [("passed", "yes" if passed else "no"),
                          ("expected", exp), ("got", got[:60] or "-")])

    if "latency" in tests:
        r = await B.latency(client, model, max_tokens=args.tokens)
        if r.ok:
            _table("Latency", [("TTFT (s)", f"{r.ttft:.3f}"), ("Total (s)", f"{r.total_time:.3f}"),
                               ("Prompt tokens", str(r.prompt_tokens)),
                               ("Completion tokens", str(r.completion_tokens)),
                               ("Decode tok/s", f"{r.output_tps:.1f}")])
        else:
            console.print(f"[red]Latency failed:[/red] {r.error}")

    if "throughput" in tests:
        results, ok = await B.throughput(client, model, max_tokens=args.tokens, runs=args.runs)
        if ok:
            _table("Throughput (avg)", [
                ("Decode tok/s", f"{statistics.mean(x.output_tps for x in ok):.1f}"),
                ("TTFT (s)", f"{statistics.mean(x.ttft for x in ok):.3f}"),
                ("Completion tokens", f"{statistics.mean(x.completion_tokens for x in ok):.0f}"),
                ("Runs", f"{len(ok)}/{len(results)}")])
        else:
            console.print(f"[red]Throughput failed:[/red] {results[0].error if results else '?'}")

    if "load" in tests:
        st = await B.load(client, model, concurrency=args.concurrency,
                          requests=args.requests, max_tokens=args.tokens)
        _table("Load", [("Success", f"{st.success}/{st.requests}"),
                        ("Wall time (s)", f"{st.wall_time:.2f}"),
                        ("Aggregate tok/s", f"{st.aggregate_tps:.1f}"),
                        ("Per-req tok/s", f"{st.per_request_tps_mean:.1f}"),
                        ("TTFT p50/p95 (s)", f"{st.ttft_p50:.3f} / {st.ttft_p95:.3f}"),
                        ("Latency p50/p95 (s)", f"{st.latency_p50:.2f} / {st.latency_p95:.2f}")])
        if st.errors:
            console.print(f"[yellow]Sample errors:[/yellow] {st.errors}")

    if "context" in tests:
        r, prefill = await B.context_test(client, model, ctx_tokens=args.ctx)
        if r.ok:
            _table("Context / prefill", [("Prompt tokens", str(r.prompt_tokens)),
                                         ("TTFT (s)", f"{r.ttft:.3f}"),
                                         ("Prefill tok/s", f"{prefill:.0f}"),
                                         ("Decode tok/s", f"{r.output_tps:.1f}")])
        else:
            console.print(f"[red]Context failed:[/red] {r.error}")


def main():
    p = argparse.ArgumentParser(
        prog="llmscanner-cli",
        description="Discover & benchmark local LLM servers (vLLM, SGLang, Ollama, llama.cpp, TGI, LM Studio).")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", help="Scan the local network for LLM servers")
    sp.add_argument("--subnet", help="CIDR, e.g. 192.168.1.0/24 (default: auto)")
    sp.add_argument("--ports", help="e.g. 8000,8080,30000 or 8000-8010")
    sp.add_argument("--timeout", type=float, default=1.0)
    sp.add_argument("--detect-timeout", type=float, default=4.0)
    sp.add_argument("--concurrency", type=int, default=256)
    sp.set_defaults(func=cmd_scan)

    dp = sub.add_parser("detect", help="Fingerprint a single host:port")
    dp.add_argument("--host", required=True)
    dp.add_argument("--port", type=int, required=True)
    dp.add_argument("--timeout", type=float, default=4.0)
    dp.set_defaults(func=cmd_detect)

    mp = sub.add_parser("models", help="List models on a server")
    mp.add_argument("--host", required=True)
    mp.add_argument("--port", type=int, default=8000)
    mp.add_argument("--api-key", default="EMPTY")
    mp.add_argument("--timeout", type=float, default=30.0)
    mp.set_defaults(func=cmd_models)

    bp = sub.add_parser("bench", help="Benchmark a server")
    bp.add_argument("--host", required=True)
    bp.add_argument("--port", type=int, default=8000)
    bp.add_argument("--model", help="Model id (default: first listed)")
    bp.add_argument("--api-key", default="EMPTY")
    bp.add_argument("--endpoint", choices=["chat", "completions"], default="chat")
    bp.add_argument("--tokens", type=int, default=256, help="max output tokens")
    bp.add_argument("--ctx", type=int, default=2048, help="approx context tokens")
    bp.add_argument("--runs", type=int, default=3)
    bp.add_argument("--concurrency", type=int, default=8)
    bp.add_argument("--requests", type=int, default=32)
    bp.add_argument("--timeout", type=float, default=120.0)
    bp.add_argument("--test", nargs="+", default=["sanity", "latency", "throughput"],
                    choices=["sanity", "latency", "throughput", "load", "context", "all"])
    bp.set_defaults(func=cmd_bench)

    args = p.parse_args()
    try:
        # A command returns a non-zero int to signal failure (bad arguments, …);
        # the console-script wrapper turns whatever main() returns into the exit
        # code, so it has to be propagated rather than dropped.
        return asyncio.run(args.func(args)) or 0
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
