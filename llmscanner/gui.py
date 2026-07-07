"""CustomTkinter desktop GUI for discovering and benchmarking local LLM servers.

The GUI thread runs Tk's mainloop. All network/async work runs on a separate
asyncio event-loop thread; results are marshalled back to the GUI via a queue.

Built on CustomTkinter for a modern, theme-aware look. The three data tables use
ttk.Treeview (CustomTkinter has no table widget); they are styled to match the
active light/dark appearance.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

import customtkinter as ctk

from . import benchmark as B
from . import icon as iconmod
from . import store
from .client import LLMClient
from .detect import smart_detect
from .scanner import DEFAULT_PORTS, detect_many, scan_network
from .util import default_subnet, parse_ports, resolve_target

APP_TITLE = "LLM Scanner — local model tester"

SUPPORT_EMAIL = "support@itteam.eu"

# UI chrome strings for the language switch (English primary, Estonian optional).
# The main tab UI stays English; this covers the settings bar, the Help window
# and the status line.
LANG = {
    "en": {
        "help": "❔ Help", "theme": "Theme", "language": "Language",
        "ready": "Ready.", "help_title": "Help & Support",
        "close": "Close",
        "theme_system": "System", "theme_light": "Light", "theme_dark": "Dark",
    },
    "et": {
        "help": "❔ Abi", "theme": "Teema", "language": "Keel",
        "ready": "Valmis.", "help_title": "Abi ja tugi",
        "close": "Sulge",
        "theme_system": "Süsteem", "theme_light": "Hele", "theme_dark": "Tume",
    },
}


# Estonian translations for the visible tab UI, keyed by the English string
# (so any untranslated literal falls back to English automatically). Covers tab
# names, section titles, field labels, primary buttons and checkboxes; the ⓘ
# deep-help tooltips and long intro paragraphs stay English.
TR_ET = {
    # tabs
    "Connection": "Ühendus", "Benchmark": "Jõudlus", "Optimum finder": "Optimeerija",
    "Soak": "Püsikoormus", "Capacity": "Võimsus",
    "Model fit": "Mudeli sobivus", "Provider fit": "Pakkuja sobivus",
    "Capabilities": "Võimekused", "Embed speed": "Embed-kiirus",
    "Embed quality": "Embed-kvaliteet", "Vision": "Nägemine",
    "Network scan": "Võrguskann", "History": "Ajalugu",
    # section titles
    "Saved hosts (quick-select)": "Salvestatud hostid (kiirvalik)",
    "Server info": "Serveri info", "Parameters": "Parameetrid",
    "Tests to run": "Käivitatavad testid", "What to find": "Mida otsida",
    "Measured operating points": "Mõõdetud tööpunktid",
    "Sustained throughput (tokens / hour)": "Püsiv läbilaskevõime (tokenit / tunnis)",
    "Token capacity (peak tokens / minute)": "Token-võimsus (tipp tokenit / minutis)",
    "Capabilities — what this endpoint/model offers":
        "Võimekused — mida see endpoint/mudel pakub",
    "Embedding speed (throughput & latency)": "Embeddingu kiirus (läbilaskevõime & latents)",
    "Embedding quality (does it actually work?)": "Embeddingu kvaliteet (kas päriselt töötab?)",
    "Vision (VL) — does the model understand images?":
        "Nägemine (VL) — kas mudel mõistab pilte?",
    "Live": "Reaalajas",
    "Model fit — Openclaw / Hermes suitability": "Mudeli sobivus — Openclaw / Hermes",
    "Provider fit — OpenRouter / HuggingFace readiness":
        "Pakkuja sobivus — OpenRouter / HuggingFace valmidus",
    "Scan settings": "Skanni seaded", "Discovered servers": "Leitud serverid",
    # field labels
    "API key": "API võti", "Concurrency": "Paralleelsus", "Endpoint": "Otspunkt",
    "Filter:": "Filter:", "Host / IP": "Host / IP", "Model": "Mudel", "Port": "Port",
    "Ports": "Pordid", "Subnet (CIDR)": "Alamvõrk (CIDR)", "Timeout (s)": "Timeout (s)",
    "Concurrency levels": "Paralleelsuse tasemed", "Concurrency sweep": "Paralleelsuse sweep",
    "Concurrency-phase ctx": "Paralleelsuse-faasi ctx", "Context probe (tok)": "Konteksti proov (tok)",
    "Context tokens": "Konteksti tokenid", "Duration (min)": "Kestus (min)",
    "Gen lengths (tok)": "Gen pikkused (tok)", "Gen tokens / req": "Gen tokenit / päring",
    "Input tokens / req": "Sisend-tokenit / päring", "Load concurrency": "Koormuse paralleelsus",
    "Load requests": "Koormuse päringud", "Max context cap": "Max konteksti lagi",
    "Max ctx probe": "Max ctx proov", "Max output tokens": "Max väljund-tokenit",
    "Min success %": "Min edu %", "Output tokens / req": "Väljund-tokenit / päring",
    "Profile concurrency": "Profiili paralleelsus", "Profiles in/out": "Profiilid sisse/välja",
    "Request sizes (tok)": "Päringu suurused (tok)", "Requests / level": "Päringuid / tase",
    "Requests per worker": "Päringuid töötaja kohta", "Settle pause (s)": "Settimispaus (s)",
    "Sweep concurrencies": "Sweep paralleelsused", "Throughput runs": "Läbilaskevõime jooksud",
    "TTFT p95 SLA (s)": "TTFT p95 SLA (s)",
    "Max concurrency": "Max paralleelsus", "Window / step (s)": "Aken / samm (s)",
    "Target tok/min (optional)": "Siht tok/min (valikuline)", "Checks": "Kontrollid",
    # buttons
    "Detect server": "Tuvasta server", "List models": "Loetle mudelid", "Load": "Lae",
    "Save current…": "Salvesta praegune…", "Delete": "Kustuta",
    "Run benchmark": "Käivita benchmark", "Find optima": "Leia optimum",
    "Run soak test": "Käivita soak-test", "Run capacity test": "Käivita võimsus-test",
    "Run capability scan": "Käivita võimekuse-skann",
    "Run embed speed test": "Käivita embed-kiiruse test",
    "Run embed quality test": "Käivita embed-kvaliteedi test",
    "Run vision test": "Käivita nägemis-test",
    "Run model-fit test": "Käivita model-fit test",
    "Run provider-fit test": "Käivita provider-fit test", "Scan network": "Skanni võrku",
    "Stop": "Peata", "Cancel": "Tühista", "Clear": "Tühjenda", "Clear all": "Tühjenda kõik",
    "Clear view": "Tühjenda vaade", "Copy to clipboard": "Kopeeri lõikelauale",
    "Export CSV…": "Ekspordi CSV…", "Refresh": "Värskenda",
    "Repeat last run": "Korda viimast jooksu", "Use selected server": "Kasuta valitud serverit",
    "Live log": "Reaalaja logi",
    # checkboxes
    "Distinct request prefixes (spread across backends)":
        "Eristuvad päringu-prefiksid (jaota backend'ide vahel)",
    "Integrity probes — token-count honesty, context recall, model quality":
        "Aususe-proovid — token-loenduse ausus, konteksti tagasikutse, mudeli kvaliteet",
    # model-fit check labels
    "Tool-calling (native OpenAI tools API, Hermes-prompt fallback)":
        "Tööriista-kutsed (natiivne OpenAI tools API, Hermes-prompti tagavara)",
    "Structured JSON output (strict, parseable, correct schema)":
        "Struktuurne JSON väljund (range, parse'itav, õige skeem)",
    "Instruction following & format discipline (no leaked reasoning)":
        "Juhiste järgimine & formaadidistsipliin (mõtlemine ei leki)",
    "Latency & throughput on these prompts": "Latents & läbilaskevõime nendel promptidel",
    "Overload probe (+25%) — check clean admission control":
        "Ülekoormuse proovik (+25%) — kontrolli puhast admission control'i",
    "Set the load and press ‘Run soak test’.": "Sea koormus ja vajuta ‘Käivita soak-test’.",
    "Set the load and press ‘Run capacity test’.": "Sea koormus ja vajuta ‘Käivita võimsus-test’.",
    "Press ‘Run capability scan’ to inventory this endpoint.":
        "Vajuta ‘Käivita võimekuse-skann’, et see endpoint kaardistada.",
    "Pick an embedding model and press ‘Run embed speed test’.":
        "Vali embedding-mudel ja vajuta ‘Käivita embed-kiiruse test’.",
    "Pick an embedding model and press ‘Run embed quality test’.":
        "Vali embedding-mudel ja vajuta ‘Käivita embed-kvaliteedi test’.",
    "Pick a VL model and press ‘Run vision test’.":
        "Vali VL-mudel ja vajuta ‘Käivita nägemis-test’.",
    "Pick the checks and press ‘Run model-fit test’.":
        "Vali kontrollid ja vajuta ‘Käivita model-fit test’.",
    "Set the traffic shape and press ‘Run provider-fit test’.":
        "Sea liikluse kuju ja vajuta ‘Käivita provider-fit test’.",
    # presets
    "Workload preset:": "Koormuse eelseade:", "Chat": "Vestlus",
    "RAG (long context)": "RAG (pikk kontekst)", "Agent / batch": "Agent / batch",
    # history: compare + report
    "Compare selected": "Võrdle valitud", "Export report": "Ekspordi raport",
    "Copy sweep table": "Kopeeri sweep-tabel", "Copy report": "Kopeeri raport",
    "Copy results": "Kopeeri tulemused",
    "Disable thinking during test (test the agentic mode)":
        "Lülita thinking testi ajaks välja (testi agentset režiimi)",
}


def _help_text(lang: str) -> str:
    """The Help window body — usage guide + infrastructure & support contacts."""
    if lang == "et":
        return (
            "LLM Scanner — juhend\n"
            "════════════════════\n\n"
            "Tööriist lokaalsete LLM-serverite (vLLM, SGLang, Ollama, llama.cpp, TGI, LM Studio) "
            "avastamiseks, benchmarkimiseks ja häälestamiseks.\n\n"
            "Vahekaardid\n"
            "───────────\n"
            "• Connection — sisesta host/port, ‘Detect server’ tuvastab serveri ja mudelid.\n"
            "• Benchmark — kiirus, latents, läbilaskevõime, kontekst, sanity, sweep, determinism, limits.\n"
            "• Optimum finder — leiab automaatselt parima paralleelsuse ja suurima päringusuuruse.\n"
            "• Soak — hoiab püsivat koormust ja mõõdab tokeneid tunnis (+ TheEye päris-koormus).\n"
            "• Model fit — kas mudel sobib agentseks kasutuseks (Hermes tööriistad, JSON, juhised).\n"
            "• Provider fit — kas backend kannatab OpenRouter/HuggingFace liiklust; API-leping, aususe-\n"
            "  testid (token-loendus, kontekst, kvaliteet), pudelikaela-analüüs, verdikt.\n"
            "• Network scan — skanni alamvõrku LLM-serverite leidmiseks.\n"
            "• History — kõik tulemused salvestuvad ~/.llmscanner ja on jooksude-vahel võrreldavad.\n\n"
            "Näpunäited\n"
            "──────────\n"
            "• Iga välja kõrval ⓘ ikoon avab selgituse.\n"
            "• Teema (hele/tume) ja keele valik on üleval paremal; valik jäetakse meelde.\n"
            "• Tõsta Timeout suurte väljundite jaoks.\n\n"
            "Infrastruktuur\n"
            "──────────────\n"
            "Visioline Infra — majutus ja infrastruktuur.\n\n"
            "Tugi ja kontakt\n"
            "───────────────\n"
            f"E-post: {SUPPORT_EMAIL}\n"
            "Küsimuste, vigade ja soovide korral kirjuta tugimeeskonnale.\n"
        )
    return (
        "LLM Scanner — user guide\n"
        "════════════════════════\n\n"
        "A tool to discover, benchmark and tune local LLM servers (vLLM, SGLang, Ollama, "
        "llama.cpp, TGI, LM Studio).\n\n"
        "Tabs\n"
        "────\n"
        "• Connection — enter host/port; ‘Detect server’ fingerprints the server and lists models.\n"
        "• Benchmark — speed, latency, throughput, context, sanity, sweep, determinism, limits.\n"
        "• Optimum finder — auto-finds the best concurrency and largest working request size.\n"
        "• Soak — holds sustained load and measures tokens/hour (+ TheEye real workload).\n"
        "• Model fit — whether a model suits agentic use (Hermes tools, JSON, instructions).\n"
        "• Provider fit — whether a backend can serve OpenRouter/HuggingFace traffic: API contract,\n"
        "  integrity probes (token counting, context, quality), bottleneck analysis, verdict.\n"
        "• Network scan — scan a subnet to discover LLM servers.\n"
        "• History — every result is saved to ~/.llmscanner and compared run-over-run.\n\n"
        "Tips\n"
        "────\n"
        "• The ⓘ icon next to each field opens an explanation.\n"
        "• Theme (light/dark) and language are top-right; your choice is remembered.\n"
        "• Raise the Timeout for large output sizes.\n\n"
        "Infrastructure\n"
        "──────────────\n"
        "Visioline Infra — hosting and infrastructure.\n\n"
        "Support & contact\n"
        "─────────────────\n"
        f"Email: {SUPPORT_EMAIL}\n"
        "For questions, bug reports and feature requests, contact the support team.\n"
    )

# Per-setting help text shown when the ⓘ icon next to a field is clicked.
INFO = {
    # ---- Connection ----
    "conn_host": (
        "Where the server is. Accepts a bare hostname or IP (127.0.0.1), host:port "
        "(10.0.0.5:8000), or a full URL (https://host/v1).\n\n"
        "• A public domain defaults to HTTPS on 443; a local IP / localhost uses HTTP "
        "on the Port field.\n"
        "• A scheme or port typed here overrides the Port field.\n"
        "• 'Detect server' auto-probes https/http candidates and writes the resolved "
        "URL back here."),
    "conn_port": (
        "TCP port of the server. Used only when the Host is a bare IP / hostname "
        "(no scheme or port of its own).\n\n"
        "Common: vLLM 8000 · SGLang 30000 · llama.cpp 8080 · Ollama 11434 · LM Studio 1234."),
    "conn_apikey": (
        "Bearer token sent as 'Authorization: Bearer <key>'. Leave as EMPTY for local "
        "servers that don't check it. Required for gateways like ApiRouter."),
    "conn_endpoint": (
        "Which OpenAI route to call:\n\n"
        "• chat → /v1/chat/completions (messages format; the usual choice).\n"
        "• completions → /v1/completions (raw prompt; older/base models)."),
    "conn_model": (
        "Model id to test (as returned by /v1/models). Leave blank to auto-select the "
        "first model the server lists. Use 'List models' / 'Detect server' to fill the "
        "dropdown."),
    # ---- Benchmark ----
    "bench_tokens": (
        "Max output tokens generated per request in the speed / load / sweep tests — "
        "the decode length. Larger = more decode work measured, but slower runs."),
    "bench_ctx": (
        "Prompt size (input tokens) for the Context / prefill test. The tool sends a "
        "prompt of about this length and measures how fast the server ingests it."),
    "bench_timeout": (
        "Per-request timeout in seconds (default 95). A request that takes longer is "
        "counted as failed. Raise it for very large prompts or long generations."),
    "bench_runs": (
        "Number of sequential requests in the throughput test. The result is the mean "
        "over these runs; more runs = steadier numbers but slower."),
    "bench_conc": (
        "Number of simultaneous requests in the Load test. Models real parallel users. "
        "Higher pushes the server harder (until it saturates or starts failing)."),
    "bench_reqs": (
        "Total requests fired in the Load test (at the concurrency above). More = a "
        "longer, steadier measurement."),
    "bench_sweep": (
        "Concurrency levels for the Concurrency-sweep test, comma-separated "
        "(e.g. 1,2,4,8,16). It runs the load test at each and charts tok/s + latency "
        "vs concurrency to find the saturation point."),
    "bench_ctxprobe": (
        "Upper bound (tokens) for the Limits test's binary search of the real max "
        "context length. Keep at or below the model's advertised limit."),
    "t_speed": (
        "Latency + throughput: one request for TTFT (time to first token) and decode "
        "tok/s, then N sequential runs for average throughput."),
    "t_load": (
        "Fires the configured concurrency × requests in parallel and reports aggregate "
        "tok/s and p50/p95 latency — how the server behaves under load."),
    "t_ctx": (
        "Sends a ~Context-tokens prompt and measures prefill (input) speed; also "
        "verifies the server accepts that context length."),
    "t_sanity": (
        "A correctness check (not just speed): asks a simple question and verifies the "
        "answer, so a fast-but-wrong server is caught."),
    "t_sweep": (
        "Runs the load test across the 'Sweep concurrencies' levels to find the "
        "throughput saturation point; charts tok/s and latency vs concurrency."),
    "t_prefix": (
        "Sends the same long prefix twice; a much faster second TTFT reveals automatic "
        "prefix caching (vLLM/SGLang)."),
    "t_determ": (
        "Sends the same prompt at temperature 0 several times and reports what % of "
        "outputs are identical — exposes batching non-determinism."),
    "t_limits": (
        "Binary-searches the real max context length, then hides a code in a long "
        "context and asks for it back (needle-in-haystack recall)."),
    # ---- Optimum finder ----
    "opt_levels": (
        "Parallel-request counts to sweep in phase B, comma-separated. The finder "
        "climbs these — early-stopping when throughput plateaus or a level starts "
        "failing — to find the best concurrency. Higher = more simultaneous requests.\n\n"
        "Cap this at your server's max concurrency (e.g. vLLM --max-num-seqs): higher "
        "levels just return 'at capacity' 429s and waste time. Default ends at 64."),
    "opt_ctxcap": (
        "Upper bound (tokens) for the phase-A max-context probe; prompts larger than "
        "this are never tried. Set it to the server's --max-model-len (default 65536). "
        "The finder prefers the server-advertised limit anyway, so this is just a ceiling."),
    "opt_basectx": (
        "Prompt size (input tokens) used during the phase-B concurrency sweep. Kept "
        "modest (default 1024) so the sweep isolates concurrency, not prompt size."),
    "opt_gentok": (
        "Max OUTPUT (answer) tokens per request — the decode length used in phases B "
        "and C.\n\n"
        "• Small (32–64): fast, but throughput is prefill-dominated.\n"
        "• Large (512+): measures real decode speed, but slower and more likely to hit "
        "the timeout under load.\n\n"
        "To test several output lengths, use 'Sweep generation lengths' below."),
    "opt_rpw": (
        "Requests fired per parallel slot at each concurrency level. E.g. concurrency 8 "
        "× 2 per worker = 16 requests. More = steadier numbers but a slower run and more "
        "total load on the server (at concurrency 64 × 2 that is already 128 requests)."),
    "opt_minok": (
        "A level counts as 'feasible' only if at least this % of its requests succeed; "
        "below it the finder stops climbing that axis. Lower (e.g. 75) tolerates flaky "
        "requests; higher is stricter."),
    "opt_frontier": (
        "Phase C. For each 'Request sizes' value, climb concurrency to find the "
        "peak-throughput and max-feasible concurrency at that prompt size — revealing "
        "the size ↔ parallelism (KV-cache) trade-off. Off = only phases A and B run."),
    "opt_sizes": (
        "Input (prompt) sizes to test in phase C, comma-separated. Each is the request "
        "size (KV-cache pressure); sizes above the detected max context are skipped."),
    "opt_gensweep": (
        "Phase D. At the recommended concurrency (the knee), test each 'Gen lengths' "
        "value to show how output length changes decode tok/s and latency. Off = skip."),
    "opt_gensizes": (
        "Output (answer) lengths to test in phase D, comma-separated (e.g. 64,256,1024). "
        "Only used when 'Sweep generation lengths' is on."),
    "opt_profiles": (
        "Phase E. Measure a few fixed (input, output) workloads at one concurrency — "
        "mirrors the standard vLLM serving benchmark (prompt-heavy / decode-heavy / "
        "balanced) so results are directly comparable to published numbers. Off = skip.\n\n"
        "Note: large output profiles (e.g. 8000) can exceed the request Timeout on "
        "slower servers — raise Timeout on the Connection/Benchmark tab if they fail."),
    "opt_proflist": (
        "Workload profiles as input/output token pairs, comma-separated. Default mirrors "
        "the NVIDIA/vLLM report: 8000/1000 (prompt-heavy), 1000/8000 (decode-heavy), "
        "1000/1000 (balanced). Each runs at the Profile concurrency below."),
    "opt_profconc": (
        "Concurrency used for the workload profiles (default 16, matching the published "
        "vLLM benchmark's 16 concurrent prompts). Fixed so only the workload shape varies."),
    "opt_settle": (
        "Pause (seconds) before EACH measurement, so the server can drain the previous "
        "batch — free KV cache, empty its queue, let a rate-limit window reset — before "
        "the next concurrency/size/profile is tested.\n\n"
        "Without it, leftover load from one level bleeds into the next (e.g. spurious "
        "'at capacity' 429s that are really just stacked requests). Default 3 s. Raise "
        "for a shared/rate-limited gateway; set 0 for a dedicated local server."),
    "opt_distinct": (
        "Give every request a unique random preamble so prefix-affinity load-balancers "
        "(e.g. ApiRouter) treat them as separate conversations and spread them across "
        "backends.\n\nOff = requests sharing a prefix may all be pinned to one GPU, "
        "under-measuring true concurrency. Turn off only to deliberately study "
        "prefix-cache/affinity behaviour."),
    # ---- Network scan ----
    "scan_subnet": (
        "The subnet to scan, in CIDR form (e.g. 192.168.1.0/24). Every host in the "
        "range is probed on the ports below. Only scan networks you own or may test."),
    "scan_timeout": (
        "Per-port TCP connect timeout in seconds. Lower = faster scan but may miss slow "
        "hosts; higher = more reliable but slower."),
    "scan_conc": (
        "How many port probes run at once. Higher = faster scans; very high may exhaust "
        "file descriptors on some systems (the tool raises the limit automatically)."),
    "scan_ports": (
        "Ports to probe on each host, comma-separated (ranges like 8000-8010 allowed). "
        "Defaults cover common LLM servers (vLLM, SGLang, Ollama, llama.cpp, …)."),
    # ---- Soak ----
    "soak_conc": (
        "How many requests to keep in flight continuously for the whole run. Set it to "
        "your throughput optimum (run the Optimum finder first) or your aggregate "
        "max concurrency. This is the sustained load the tokens/hour figure is measured at."),
    "soak_in": (
        "Input (prompt) tokens per request — the 'question' size. Pick what's typical of "
        "your real traffic (e.g. 4000 for RAG/long-context, ~500 for chat). Bigger input = "
        "more tokens IN per hour, more prefill work."),
    "soak_out": (
        "Max output tokens per request — the 'answer' size. Forced with ignore_eos so each "
        "request decodes this many. Bigger output = more tokens OUT per hour, longer requests."),
    "soak_dur": (
        "How long to run, in minutes. A longer run (30+) reveals whether throughput holds "
        "steady or drifts (thermal throttling, memory, gateway hiccups). The tokens/hour "
        "figure is the sustained average over the whole run."),
    "soak_distinct": (
        "Give each request a unique preamble so a prefix-affinity gateway spreads the load "
        "across all backends (essential for a multi-machine cluster — otherwise it pins to "
        "one). Keep on unless you deliberately want single-backend numbers."),
    "soak_theeye": (
        "Replay the real TheEye production traffic mix instead of one fixed request size. "
        "Each request samples a task type (weighted by its real call rate — classification, "
        "social_image_understand, extraction, entity_update, …) and draws input/output token "
        "counts from that task's measured distribution (lognormal fit to mean/p95).\n\n"
        "This gives a realistic sustained tokens/hour for your actual workload — mostly short "
        "structured calls (~1.3k in / ~150 out) with an occasional heavy entity generation. "
        "The Input/Output token fields are ignored; you only set duration and concurrency."),
    "soak_overload": (
        "Run at 10% ABOVE the Concurrency (e.g. 64 → 72) to test admission control: a good "
        "gateway (OpenRouter, HuggingFace, a well-configured vLLM) rejects the overflow "
        "cleanly with HTTP 429/503 while the rest succeed.\n\n"
        "The result verdict tells you which happened:\n"
        "• clean 429/503 for the excess → proper backpressure ✓\n"
        "• no rejections but output truncated / degraded → no admission control ⚠\n"
        "• timeouts / hard errors → the server breaks under overload ✗"),
    # ---- Capacity (tok/min ceiling) ----
    "cap_max_conc": (
        "The highest concurrency to ramp up to. The test steps 1 → 2 → 4 → … up to this "
        "value, measuring the delivered tok/min at each level, and stops early once "
        "throughput plateaus or the server starts rejecting. If the run ends with 'still "
        "climbing', raise this to find the true ceiling."),
    "cap_in": (
        "Input (prompt) tokens per request — the 'question' size. Use what's typical of "
        "your real traffic. Total capacity counts prompt + completion tokens, so a larger "
        "input raises the tok/min figure (more prefill work per request)."),
    "cap_out": (
        "Max output tokens per request — the 'answer' size, forced with ignore_eos so every "
        "request decodes this many. Larger output = more decode work, fewer requests/min."),
    "cap_window": (
        "How long to hold each concurrency level, in seconds. The first ~third is discarded "
        "as warm-up (queue fill / cold KV cache); the steady-state tok/min is measured over "
        "the rest. 30–60 s per step gives a stable number without dragging the whole ramp out.\n\n"
        "Make it comfortably LONGER than one request takes (see latency p95 in Benchmark) — "
        "if no request finishes inside the window, that level can't be measured and the run "
        "stops with a 'raise Window / step' note."),
    "cap_target": (
        "Optional. Your required capacity in tokens per minute (e.g. a contracted TPM or the "
        "peak load you must serve). If set, the result adds a PASS/FAIL: does the measured "
        "peak capacity meet it? Leave empty to just measure the ceiling."),
    # ---- Embed speed ----
    "emb_model": (
        "The embedding model to test (POSTed to /v1/embeddings). This is usually a DIFFERENT "
        "model from your chat model — e.g. bge-m3, e5, nomic-embed. Leave empty to use the model "
        "selected at the top. Run the Capabilities tab first to see which model embeds. A quick "
        "preflight embed runs before the test; if the model can't embed, the test stops with a "
        "clear message."),
    "emb_batch": (
        "Texts per request — embedding servers batch efficiently, so a bigger batch usually means "
        "far more embeddings/second (up to the server's limit). 32 is a good start; try 8 / 64 / 128 "
        "to find where throughput stops rising."),
    "emb_conc": (
        "How many batch requests to keep in flight at once. Raise it until embeddings/second stops "
        "climbing (the server is saturated) or errors appear."),
    "emb_intok": (
        "Approximate tokens per text — the length of each item embedded. Short (~64) is typical for "
        "search queries / chunks; raise it for long-document embedding. Longer texts = more tokens/s "
        "of work but fewer embeddings/s."),
    "emb_dur": (
        "How long to hold the load, in seconds. 15–30 s gives a stable sustained rate. The test "
        "reports embeddings/s, input tokens/s, requests/s and per-request latency over the run."),
    "vis_model": (
        "The vision-language (VL) model to test — one that accepts image input (e.g. a Qwen2.5-VL, "
        "Llama-3.2-Vision, InternVL, Pixtral, LLaVA). Leave empty to use the model selected at the "
        "top. The test sends generated images with known content and checks the answers; a text-only "
        "model fails the first probe (image not accepted) and the rest are skipped."),
    # ---- Model fit (Openclaw / Hermes) ----
    "fit_tool": (
        "Function calling. The model is given tools via the native OpenAI `tools` "
        "request parameter (what routers / vLLM / TGI / SGLang implement); a model "
        "that only knows the prompt convention is also credited if it emits a Hermes "
        "<tool_call> in the text. Scored on: does it call a tool, pick the right one, "
        "fill the right arguments, and — crucially — NOT call a tool when the query "
        "needs a plain answer. The core agentic capability."),
    "fit_json": (
        "Strict JSON output. The model is asked for a specific JSON shape with no prose "
        "or code fences. Scored on whether the reply parses as JSON and matches the "
        "requested keys/types — what breaks a pipeline that json.loads() the output."),
    "fit_instruct": (
        "Instruction following & format discipline. Tight formatting orders (exactly "
        "one word, only a number, three lines) plus a check that the model doesn't leak "
        "reasoning / <think> scaffolding into the visible answer an agent has to parse."),
    "fit_latency": (
        "Measures response latency and output tok/s across every probe above, so you "
        "see whether the model is fast enough for interactive agentic use, not just "
        "whether it's correct."),
    # ---- Provider fit (OpenRouter / HuggingFace) ----
    "rd_in": (
        "Input (prompt) tokens per request in the concurrency sweep — the request "
        "shape the load is measured at. ~1k models a typical chat/RAG turn; raise it "
        "to stress prefill, lower it for short-prompt chat traffic."),
    "rd_out": (
        "Output tokens per request, forced with ignore_eos so every request decodes "
        "this many. Governs how decode-heavy the traffic is."),
    "rd_sweep": (
        "Concurrency levels to sweep, comma-separated (e.g. 1,4,8,16,32). Each level "
        "runs a short load batch; comparing them reveals where throughput stops "
        "scaling (the knee) and whether latency degrades — the bottleneck signature."),
    "rd_reqs": (
        "Requests per sweep level (at least 2× the concurrency is used, whichever is "
        "larger) so each level reaches steady state. Higher = steadier numbers, longer run."),
    "rd_sla": (
        "TTFT p95 target in seconds. A provider verdict counts TTFT as passing when the "
        "95th-percentile time-to-first-token AT THE THROUGHPUT KNEE stays under this. "
        "Routers care about first-token latency; 2–3s is a reasonable local target."),
    "rd_ctx": (
        "How deep to probe context honesty (tokens). Hides a secret code at several "
        "depths in a prompt of this size and asks for it back — catches a server that "
        "silently truncates or whose advertised context length isn't real. Capped to "
        "the server's advertised max when it exposes one."),
    "rd_integrity": (
        "Adversarial honesty probes a router runs on a backend it doesn't control:\n"
        "• Token-count honesty — forces a known output length and compares the server's "
        "reported completion_tokens against the actual text; catches billing inflation.\n"
        "• Context honesty — needle-in-a-haystack recall (see Context probe).\n"
        "• Model quality — a golden-answer eval; a silently quantised / wrong / broken "
        "model drops these.\n"
        "• Logprob fingerprint — the model's confidence on a trivial fact (informational; "
        "many servers don't expose logprobs)."),
    "rd_nothink": (
        "Send chat_template_kwargs.enable_thinking=false on every request, so a "
        "Qwen3-style reasoning model is tested in its non-thinking (agentic) mode.\n\n"
        "Why on by default: Provider fit measures whether the backend can serve "
        "agentic / tool-calling traffic, and in thinking mode such a model tends to "
        "'overthink' — it reasons in prose and answers directly instead of emitting a "
        "tool call, so the tool-calling probes fail even though the model is capable. "
        "Turning thinking off tests the mode that actually handles tools.\n\n"
        "Uncheck it to test the thinking variant as-is. Servers that don't support the "
        "parameter simply ignore it."),
    "rd_overload": (
        "After the sweep, run one extra level 25% above the top concurrency to test "
        "admission control: a router-grade backend rejects the excess cleanly with "
        "429/503 rather than accepting it and timing out."),
    "rd_distinct": (
        "Give each request a unique preamble so a prefix-affinity gateway spreads the "
        "load across backends instead of pinning it to one GPU (see the Soak tab). Keep "
        "on for cluster-realistic numbers."),
    # ---- History ----
    "hist_filter": (
        "Type to filter the results list — matches host, model, test type and summary. "
        "Clear with the ✕ button. All results are saved permanently to ~/.llmscanner."),
}


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _is_dark() -> bool:
    try:
        return ctk.get_appearance_mode() == "Dark"
    except Exception:
        return True


def _palette() -> dict:
    """Colors for the ttk tables / chart, matched to the active appearance."""
    if _is_dark():
        return {
            "tree_bg": "#2b2b2b", "tree_fg": "#dce4ee", "tree_field": "#2b2b2b",
            "head_bg": "#1d1e1e", "head_fg": "#dce4ee", "sel": "#1f6aa5",
            "header_tag": ("#2d4a63", "#eaf2fb"), "when_tag": ("#3a3a3a", "#cfd6de"),
            "row_odd": "#2b2b2b", "row_even": "#313131",
            "canvas_bg": "#2b2b2b", "grid": "#3a3a3a", "axis": "#5a5a5a",
            "txt": "#dce4ee", "sub": "#8a93a0", "line": "#3b8ed0", "warn": "#e0a060",
            "live_bg": "#202020", "live_ts": "#6f7885", "live_head": "#7fb2e0",
            "live_ok": "#57c07a", "live_err": "#e26d6d", "live_dim": "#9aa3af",
        }
    return {
        "tree_bg": "#ffffff", "tree_fg": "#1a1a1a", "tree_field": "#ffffff",
        "head_bg": "#e6e6e6", "head_fg": "#1a1a1a", "sel": "#3b8ed0",
        "header_tag": ("#dbe4ff", "#1e293b"), "when_tag": ("#eef2ff", "#334155"),
        "row_odd": "#ffffff", "row_even": "#f3f5f9",
        "canvas_bg": "#ffffff", "grid": "#eeeeee", "axis": "#c8c8c8",
        "txt": "#333333", "sub": "#888888", "line": "#2563eb", "warn": "#a05000",
        "live_bg": "#f7f8fa", "live_ts": "#94a0b0", "live_head": "#1e5fa8",
        "live_ok": "#1c8a44", "live_err": "#b23b3b", "live_dim": "#6b7280",
    }


class AsyncRunner:
    """Owns a background asyncio event loop running in its own thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro, done_cb):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(done_cb)
        return fut

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


class ChartCanvas(tk.Canvas):
    """A tiny dependency-free, appearance-aware line chart on a tk.Canvas.

    Points are (label, value) or (label, value, kind) tuples; kind "err"/"warn"
    colours that marker (e.g. a saturated load level). `mark` names one index to
    ring-highlight with a caption (e.g. the capacity peak).
    """

    def __init__(self, parent, height=160, **kw):
        super().__init__(parent, height=height, bg=_palette()["canvas_bg"],
                         highlightthickness=0, **kw)
        self._points: list[tuple] = []
        self._title = ""
        self._unit = ""
        self._mark: int | None = None
        self._mark_text = ""
        self.bind("<Configure>", lambda e: self._redraw())

    def plot(self, points, title="", unit="", mark: int | None = None, mark_text=""):
        self._points = list(points)
        self._title = title
        self._unit = unit
        self._mark = mark
        self._mark_text = mark_text
        self._redraw()

    def clear(self):
        self._points = []
        self._title = ""
        self._mark = None
        self._mark_text = ""
        self._redraw()

    @staticmethod
    def _fmt_val(v: float) -> str:
        """Compact number for axis / point labels (31 242 857 → '31.2M')."""
        a = abs(v)
        if a >= 1e9:
            return f"{v / 1e9:.1f}B"
        if a >= 1e6:
            return f"{v / 1e6:.1f}M"
        if a >= 1e4:
            return f"{v / 1e3:.0f}K"
        if a >= 1e3:
            return f"{v / 1e3:.1f}K"
        return f"{v:.1f}" if a < 10 and v != int(v) else f"{v:.0f}"

    def _redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        p = _palette()
        self.configure(bg=p["canvas_bg"])
        if self._title:
            self.create_text(8, 4, anchor="nw", text=self._title, fill=p["txt"],
                             font=("TkDefaultFont", 11, "bold"))
        pts = [(i, t[1]) for i, t in enumerate(self._points) if t[1] is not None]
        if not pts:
            self.create_text(w // 2, h // 2, text="(no data — run a test to chart it)",
                             fill=p["sub"])
            return
        ys = [v for _i, v in pts]
        vmin, vmax = min(ys), max(ys)
        if vmax == vmin:
            vmax, vmin = vmax + 1.0, max(0.0, vmin - 1.0)
        pad_l, pad_r, pad_t, pad_b = 56, 16, 26, 28
        x0, x1 = pad_l, w - pad_r
        y0, y1 = h - pad_b, pad_t
        n = len(self._points)

        def sx(i):
            return (x0 + x1) / 2 if n <= 1 else x0 + (x1 - x0) * i / (n - 1)

        def sy(v):
            return y0 + (y1 - y0) * (v - vmin) / (vmax - vmin)

        self.create_line(x0, y0, x1, y0, fill=p["axis"])
        self.create_line(x0, y0, x0, y1, fill=p["axis"])
        for frac in (0.0, 0.5, 1.0):
            yv = vmin + (vmax - vmin) * frac
            yy = sy(yv)
            if frac != 0.0:
                self.create_line(x0, yy, x1, yy, fill=p["grid"])
            self.create_text(x0 - 6, yy, anchor="e", text=self._fmt_val(yv),
                             fill=p["sub"], font=("TkDefaultFont", 8))

        coords = []
        for i, v in pts:
            coords += [sx(i), sy(v)]
        if len(coords) >= 4:
            self.create_line(*coords, fill=p["line"], width=2)
        kind_fill = {"err": p["live_err"], "warn": p["warn"]}
        for i, v in pts:
            x, y = sx(i), sy(v)
            kind = self._points[i][2] if len(self._points[i]) > 2 else None
            fill = kind_fill.get(kind, p["line"])
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=fill, outline="")
        # Ring-highlight the marked point (e.g. the measured capacity peak).
        if self._mark is not None:
            mv = dict(pts).get(self._mark)
            if mv is not None:
                x, y = sx(self._mark), sy(mv)
                self.create_oval(x - 6, y - 6, x + 6, y + 6,
                                 outline=p["live_ok"], width=2)
                if self._mark_text:
                    anchor = "sw" if x < (x0 + x1) / 2 else "se"
                    self.create_text(x + (8 if anchor == "sw" else -8), y - 6,
                                     anchor=anchor, text=self._mark_text,
                                     fill=p["live_ok"], font=("TkDefaultFont", 8, "bold"))
        li, lv = pts[-1]
        if li != self._mark:
            self.create_text(sx(li), sy(lv) - 9, text=self._fmt_val(lv), fill=p["txt"],
                             font=("TkDefaultFont", 8, "bold"))
        self.create_text(x0, y0 + 4, anchor="nw", text=self._points[pts[0][0]][0],
                         fill=p["sub"], font=("TkDefaultFont", 8))
        if len(pts) > 1:
            self.create_text(x1, y0 + 4, anchor="ne", text=self._points[pts[-1][0]][0],
                             fill=p["sub"], font=("TkDefaultFont", 8))
        if self._unit:
            self.create_text(x1, y1 - 4, anchor="ne", text=self._unit, fill=p["sub"],
                             font=("TkDefaultFont", 8))


class LiveLog(ctk.CTkFrame):
    """A scrolling, colour-tagged log pane with a header + Clear button.

    Reused by any tab that streams progress (Benchmark, Optimum finder). All
    mutating methods must be called on the UI thread (post from workers).
    """

    def __init__(self, parent, pal, *, title="Live log", **kw):
        super().__init__(parent, **kw)
        self.pal = pal
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=6, pady=(6, 0))
        ctk.CTkLabel(head, text=title, anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkButton(head, text="Clear", width=56, height=24, fg_color="gray40",
                      hover_color="gray30", command=self.clear).pack(side="right")

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=6, pady=6)
        self.text = tk.Text(wrap, wrap="word", bg=pal["live_bg"], fg=pal["txt"],
                            relief="flat", borderwidth=0, highlightthickness=0,
                            padx=10, pady=8, font=("TkFixedFont", 11),
                            state="disabled", cursor="arrow")
        vsb = ctk.CTkScrollbar(wrap, command=self.text.yview)
        self.text.configure(yscrollcommand=vsb.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        bold = ("TkFixedFont", 11, "bold")
        self.text.tag_configure("ts", foreground=pal["live_ts"])
        self.text.tag_configure("head", foreground=pal["live_head"], font=bold)
        self.text.tag_configure("ok", foreground=pal["live_ok"], font=bold)
        self.text.tag_configure("err", foreground=pal["live_err"], font=bold)
        self.text.tag_configure("metric", foreground=pal["txt"])
        self.text.tag_configure("dim", foreground=pal["live_dim"])
        self.clear()

    def retheme(self, pal):
        """Re-apply colours after a light/dark switch (keeps the logged text)."""
        self.pal = pal
        bold = ("TkFixedFont", 11, "bold")
        self.text.configure(bg=pal["live_bg"], fg=pal["txt"])
        self.text.tag_configure("ts", foreground=pal["live_ts"])
        self.text.tag_configure("head", foreground=pal["live_head"], font=bold)
        self.text.tag_configure("ok", foreground=pal["live_ok"], font=bold)
        self.text.tag_configure("err", foreground=pal["live_err"], font=bold)
        self.text.tag_configure("metric", foreground=pal["txt"])
        self.text.tag_configure("dim", foreground=pal["live_dim"])

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def get_text(self) -> str:
        """The full logged transcript, as shown — for copying/sharing a run."""
        return self.text.get("1.0", "end-1c")

    def write(self, msg: str, kind: str = "head"):
        self.text.configure(state="normal")
        # Keep the log bounded during long sessions.
        if int(self.text.index("end-1c").split(".")[0]) > 800:
            self.text.delete("1.0", "200.0")
        self.text.insert("end", time.strftime("%H:%M:%S  "), ("ts",))
        self.text.insert("end", msg + "\n", (kind,))
        self.text.see("end")
        self.text.configure(state="disabled")

    def result(self, title: str, summary: str, rows, *, failed: bool | None = None):
        if failed is None:
            failed = summary.startswith("error") or summary.startswith("❌")
        self.text.configure(state="normal")
        self.text.insert("end", time.strftime("%H:%M:%S  "), ("ts",))
        self.text.insert("end", f"{title}: {summary}\n", ("err" if failed else "ok",))
        for k, v in rows:
            self.text.insert("end", f"        {k}: ", ("dim",))
            self.text.insert("end", f"{v}\n", ("metric",))
        self.text.see("end")
        self.text.configure(state="disabled")


class App:
    CMP_MAX_RUNS = 30  # cap columns in the side-by-side comparison

    def __init__(self, root: ctk.CTk):
        self.root = root
        self._lang = store.get_setting("language", "en")
        if self._lang not in LANG:
            self._lang = "en"
        self.pal = _palette()
        self.root.title(APP_TITLE)
        # Size to the screen rather than a fixed constant — on a smaller display
        # (laptop, external monitor, tiled window) a hardcoded 1400x1010 can open
        # larger than the screen itself. Clamp + center.
        w, h = self._fit_size(1400, 1010)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        # minsize is a floor the user can shrink the window down to — it must never
        # exceed the size just set above, or Tk immediately grows the window to
        # satisfy it, undoing the screen-fit clamp on a small display.
        self.root.minsize(min(1000, w), min(680, h))
        self._set_app_icon()
        self._style_trees()
        self.runner = AsyncRunner()
        self.ui_queue: "queue.Queue" = queue.Queue()
        self._busy = False
        self._scan_holder = {"done": 0, "total": 1, "phase": "scan"}
        self._current_fut = None
        self._cancel_btns = []
        self._last_run = None
        self._run_cfgs = {}
        self._run_order = []
        self._cmp_iid_test = {}
        self._cmp_data = []
        self._hist_by_iid = {}
        self._all_history = []
        self._hist_sort = ("when", True)
        self._opt_iids = {}
        self._opt_points = []

        # Shared connection variables.
        self.var_host = tk.StringVar(value="127.0.0.1")
        self.var_port = tk.StringVar(value="8000")
        self.var_apikey = tk.StringVar(value="EMPTY")
        self.var_endpoint = tk.StringVar(value="chat")
        self.var_model = tk.StringVar(value="")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(60, self._drain_queue)

    def _set_app_icon(self):
        try:
            self._icon_img = tk.PhotoImage(file=str(iconmod.ensure_icon()))
            self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _style_trees(self):
        """Style ttk.Treeview to match the CustomTkinter appearance."""
        style = ttk.Style()
        try:
            style.theme_use("default")
        except Exception:
            pass
        p = self.pal
        style.configure("Treeview", background=p["tree_bg"], foreground=p["tree_fg"],
                        fieldbackground=p["tree_field"], borderwidth=0, rowheight=28,
                        font=("TkDefaultFont", 11))
        style.configure("Treeview.Heading", background=p["head_bg"], foreground=p["head_fg"],
                        relief="flat", padding=(8, 6), font=("TkDefaultFont", 11, "bold"))
        style.map("Treeview", background=[("selected", p["sel"])],
                  foreground=[("selected", "#ffffff")])
        # Highlight the heading under the cursor so drag-to-reorder feels alive.
        style.map("Treeview.Heading",
                  background=[("active", p["sel"]), ("pressed", p["sel"])],
                  foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
                  cursor=[("active", "hand2")])

    # ----------------------------------------------------------------- helpers
    def _section(self, parent, title):
        """A titled card frame; returns (outer, body) — pack `outer`, fill `body`."""
        outer = ctk.CTkFrame(parent)
        ctk.CTkLabel(outer, text=self.L(title), anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=12, pady=(8, 0))
        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        return outer, body

    def _info_icon(self, parent, title, text):
        """A small clickable ⓘ that opens an explanation dialog for a setting."""
        ic = ctk.CTkLabel(parent, text="ⓘ", width=16, cursor="hand2",
                          text_color=self.pal["line"],
                          font=ctk.CTkFont(size=14, weight="bold"))
        ic.bind("<Button-1>", lambda _e: messagebox.showinfo(f"{title}", text))
        return ic

    def _lbl(self, parent, text, info, *, title=None):
        """A [label + ⓘ] frame to grid/pack where a plain field label would go."""
        fr = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(fr, text=self.L(text)).pack(side="left")
        self._info_icon(fr, title or text.rstrip(":"), info).pack(side="left", padx=(3, 0))
        return fr

    def _tree_with_scrollbars(self, parent, columns, *, height=8, horizontal=False,
                              reorder=False):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        tree = ttk.Treeview(wrap, columns=columns, show="headings", height=height)
        vsb = ctk.CTkScrollbar(wrap, orientation="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        if horizontal:
            hsb = ctk.CTkScrollbar(wrap, orientation="horizontal", command=tree.xview)
            tree.configure(xscrollcommand=hsb.set)
            hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        tree.tag_configure("odd", background=self.pal["row_odd"])
        tree.tag_configure("even", background=self.pal["row_even"])
        if reorder:
            self._enable_column_reorder(tree)
        return wrap, tree

    def _enable_column_reorder(self, tree: ttk.Treeview):
        """Let the user drag column headings left/right to reorder them.

        Columns stay individually resizable (dragging a separator still resizes);
        only a press-and-move that starts on a heading body reorders. Sets
        `tree._reordered` so a sort binding can ignore the release that followed.
        """
        def on_press(e):
            # Clear the guard on every fresh click so a reorder that doesn't end
            # up firing the sort command can't swallow the next genuine sort.
            tree._reordered = False
            tree._drag_from = (tree.identify_column(e.x)
                               if tree.identify_region(e.x, e.y) == "heading" else None)

        def on_release(e):
            src = getattr(tree, "_drag_from", None)
            tree._drag_from = None
            if not src or tree.identify_region(e.x, e.y) != "heading":
                return
            dst = tree.identify_column(e.x)
            if not dst or dst == src:
                return
            disp = list(tree.cget("displaycolumns"))
            if not disp or tuple(disp) == ("#all",):
                disp = list(tree.cget("columns"))
            si, di = int(src[1:]) - 1, int(dst[1:]) - 1
            if not (0 <= si < len(disp) and 0 <= di < len(disp)):
                return
            name = disp.pop(si)
            disp.insert(di, name)
            tree.configure(displaycolumns=disp)
            tree._reordered = True  # suppress the sort that this release may trigger

        tree.bind("<ButtonPress-1>", on_press, add="+")
        tree.bind("<ButtonRelease-1>", on_release, add="+")

    # ------------------------------------------------------- settings / i18n
    def t(self, key: str) -> str:
        """Translate a chrome string into the selected language (falls back to EN)."""
        return LANG.get(self._lang, LANG["en"]).get(key) or LANG["en"].get(key, key)

    def L(self, s: str) -> str:
        """Translate a tab-UI literal (English is the key; untranslated → English)."""
        return TR_ET.get(s, s) if self._lang == "et" else s

    def _fit_size(self, want_w: int, want_h: int, *,
                 wfrac: float = 0.92, hfrac: float = 0.88) -> tuple:
        """(w, h) clamped to fit the current screen — never larger than the
        display it opens on."""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = max(1, min(want_w, int(sw * wfrac)))
        h = max(1, min(want_h, int(sh * hfrac)))
        return w, h

    def _sized_geometry(self, want_w: int, want_h: int, **kw) -> str:
        """A `WxH+X+Y` geometry string sized to fit the current screen — clamped so
        the window/dialog is never larger than the display it opens on, and
        centered. Used for the main window and any Toplevel (Help, Compare)."""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = self._fit_size(want_w, want_h, **kw)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        return f"{w}x{h}+{x}+{y}"

    def _build_settings_bar(self):
        """Top-right bar: Help, theme (light/dark/system) and language switch."""
        bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(bar, text=APP_TITLE,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        self._btn_help = ctk.CTkButton(bar, text=self.t("help"), width=90,
                                       command=self._show_help)
        self._btn_help.pack(side="right", padx=(8, 0))

        # Language (English primary, Estonian optional).
        self._lang_menu = ctk.CTkOptionMenu(
            bar, width=110, values=["English", "Eesti"],
            command=self._on_language)
        self._lang_menu.set("Eesti" if self._lang == "et" else "English")
        self._lang_menu.pack(side="right", padx=(8, 0))
        self._lbl_language = ctk.CTkLabel(bar, text=self.t("language"))
        self._lbl_language.pack(side="right", padx=(12, 4))

        # Theme (System / Light / Dark).
        cur = store.get_setting("appearance", "System")
        self._theme_seg = ctk.CTkSegmentedButton(
            bar, values=["System", "Light", "Dark"], command=self._on_theme)
        self._theme_seg.set(cur if cur in ("System", "Light", "Dark") else "System")
        self._theme_seg.pack(side="right")
        self._lbl_theme = ctk.CTkLabel(bar, text=self.t("theme"))
        self._lbl_theme.pack(side="right", padx=(12, 4))

    def _on_theme(self, mode: str):
        ctk.set_appearance_mode(mode)
        store.set_setting("appearance", mode)
        self._retheme()

    def _retheme(self):
        """Re-apply palette-dependent colours after a light/dark switch — CTk
        widgets update themselves; the custom tk canvas/text/tree do not."""
        self.pal = _palette()
        self._style_trees()
        for name in ("bench_log", "opt_log", "soak_log", "capacity_log", "embed_log",
                     "fit_log", "ready_log"):
            log = getattr(self, name, None)
            if log is not None:
                log.retheme(self.pal)
        for name in ("bench_chart", "opt_chart", "soak_chart", "capacity_chart",
                     "embed_chart", "ready_chart"):
            ch = getattr(self, name, None)
            if ch is not None:
                try:
                    ch._redraw()
                except Exception:
                    pass

    def _on_language(self, choice: str):
        self._lang = "et" if choice == "Eesti" else "en"
        store.set_setting("language", self._lang)
        self._apply_language()

    def _apply_language(self):
        """Relabel the chrome and rebuild the tabs in the selected language."""
        self._btn_help.configure(text=self.t("help"))
        self._lbl_language.configure(text=self.t("language"))
        self._lbl_theme.configure(text=self.t("theme"))
        win = getattr(self, "_help_win", None)
        if win is not None and win.winfo_exists():
            self._render_help(win)
        if self._busy:
            # Can't safely rebuild mid-run; the chrome switches now, tabs on the
            # next switch or restart.
            self._set_status("Language applies to tabs when the current run finishes.")
            return
        self._rebuild_tabs()
        self.status.configure(text=self.t("ready"))

    def _show_help(self):
        win = getattr(self, "_help_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        win = ctk.CTkToplevel(self.root)
        win.title(self.t("help_title"))
        win.geometry(self._sized_geometry(720, 620))
        win.transient(self.root)
        self._help_win = win
        self._render_help(win)

    def _render_help(self, win):
        for w in win.winfo_children():
            w.destroy()
        win.title(self.t("help_title"))
        box = ctk.CTkTextbox(win, wrap="word", font=("TkDefaultFont", 13))
        box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        box.insert("1.0", _help_text(self._lang))
        box.configure(state="disabled")
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(footer, text=SUPPORT_EMAIL, width=200,
                      command=lambda: self._copy_to_clipboard(SUPPORT_EMAIL)).pack(side="left")
        ctk.CTkButton(footer, text=self.t("close"), width=90,
                      command=win.destroy).pack(side="right")

    def _copy_to_clipboard(self, text: str):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status(f"Copied: {text}")
        except Exception:
            pass

    # ----------------------------------------------------------------- UI build
    def _build_ui(self):
        self._build_settings_bar()
        self._build_tabs()

        bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(0, 8))
        self._status_bar = bar
        self.progress = ctk.CTkProgressBar(bar, mode="indeterminate", width=180)
        self.progress.set(0)
        self.progress.pack(side="right", padx=6)
        self.status = ctk.CTkLabel(bar, text=self.t("ready"), anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        # Keyboard shortcuts (bound on root, so they survive a tabview rebuild).
        self.root.bind("<Command-r>", self._run_active_tab)
        self.root.bind("<Command-period>", lambda e: self.cancel_current())
        self.root.bind("<Escape>", lambda e: self.cancel_current())
        self.root.bind("<Command-d>", lambda e: self.on_detect())
        self.root.bind("<Command-l>", lambda e: self.on_list_models())

    def _build_tabs(self):
        """Create the tabview and all tabs. Split out so a language switch can
        rebuild it (tab names are set at creation and can't be renamed live)."""
        self._cancel_btns = []
        self.tabview = ctk.CTkTabview(self.root)
        if getattr(self, "_status_bar", None) is not None:
            # Keep the tabview above the status bar when rebuilt.
            self.tabview.pack(fill="both", expand=True, padx=8, pady=(8, 4),
                              before=self._status_bar)
        else:
            self.tabview.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.tab_conn = self.tabview.add(self.L("Connection"))
        self.tab_bench = self.tabview.add(self.L("Benchmark"))
        self.tab_opt = self.tabview.add(self.L("Optimum finder"))
        self.tab_soak = self.tabview.add(self.L("Soak"))
        self.tab_capacity = self.tabview.add(self.L("Capacity"))
        self.tab_fit = self.tabview.add(self.L("Model fit"))
        self.tab_ready = self.tabview.add(self.L("Provider fit"))
        self.tab_caps = self.tabview.add(self.L("Capabilities"))
        self.tab_embed = self.tabview.add(self.L("Embed speed"))
        self.tab_embq = self.tabview.add(self.L("Embed quality"))
        self.tab_vision = self.tabview.add(self.L("Vision"))
        self.tab_scan = self.tabview.add(self.L("Network scan"))
        self.tab_history = self.tabview.add(self.L("History"))

        self._build_conn_tab()
        self._build_bench_tab()
        self._build_opt_tab()
        self._build_soak_tab()
        self._build_capacity_tab()
        self._build_modelfit_tab()
        self._build_readiness_tab()
        self._build_capabilities_tab()
        self._build_embed_tab()
        self._build_embed_quality_tab()
        self._build_vision_tab()
        self._build_scan_tab()
        self._build_history_tab()
        self._style_trees()

    def _rebuild_tabs(self):
        """Rebuild the tabview in the current language (blocked while a test runs)."""
        if self._busy:
            return
        try:
            self.tabview.destroy()
        except Exception:
            pass
        self._build_tabs()

    def _conn_fields(self, parent):
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=10)
        self._lbl(grid, "Host / IP", INFO["conn_host"]).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=5)
        self.host_combo = ctk.CTkComboBox(grid, variable=self.var_host, width=180,
                                          command=lambda _v: self._on_host_pick())
        self.host_combo.grid(row=0, column=1, sticky="w", padx=6)
        self._lbl(grid, "Port", INFO["conn_port"]).grid(row=0, column=2, sticky="w", padx=(12, 6))
        self.port_combo = ctk.CTkComboBox(grid, variable=self.var_port, width=110)
        self.port_combo.grid(row=0, column=3, sticky="w", padx=6)

        self._lbl(grid, "API key", INFO["conn_apikey"]).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=5)
        ctk.CTkEntry(grid, textvariable=self.var_apikey, width=180).grid(row=1, column=1, sticky="w", padx=6)
        self._lbl(grid, "Endpoint", INFO["conn_endpoint"]).grid(row=1, column=2, sticky="w", padx=(12, 6))
        ctk.CTkOptionMenu(grid, variable=self.var_endpoint,
                          values=["chat", "completions"], width=140).grid(row=1, column=3, sticky="w", padx=6)

        self._lbl(grid, "Model", INFO["conn_model"]).grid(row=2, column=0, sticky="w", padx=(0, 6), pady=5)
        self.model_combo = ctk.CTkComboBox(grid, variable=self.var_model, width=420)
        self.model_combo.grid(row=2, column=1, columnspan=3, sticky="w", padx=6)

    def _build_conn_tab(self):
        sec, body = self._section(self.tab_conn, "Saved hosts (quick-select)")
        sec.pack(fill="x", padx=12, pady=(12, 4))
        self.host_select = ctk.CTkOptionMenu(body, width=320,
                                             values=["(no saved hosts)"],
                                             command=lambda _v: self.on_load_host())
        self.host_select.grid(row=0, column=0, padx=6, pady=4, sticky="w")
        ctk.CTkButton(body, text=self.L("Load"), width=70, command=self.on_load_host).grid(row=0, column=1, padx=4)
        ctk.CTkButton(body, text=self.L("Save current…"), width=120, command=self.on_save_host).grid(row=0, column=2, padx=4)
        ctk.CTkButton(body, text=self.L("Delete"), width=70, fg_color="#b04a4a", hover_color="#963c3c",
                      command=self.on_delete_host).grid(row=0, column=3, padx=4)
        self._refresh_hosts()

        self._conn_fields(self.tab_conn)
        self._refresh_host_suggestions()

        btns = ctk.CTkFrame(self.tab_conn, fg_color="transparent")
        btns.pack(fill="x", padx=12)
        self.btn_detect = ctk.CTkButton(btns, text=self.L("Detect server"), command=self.on_detect)
        self.btn_detect.pack(side="left")
        self.btn_models = ctk.CTkButton(btns, text=self.L("List models"), command=self.on_list_models)
        self.btn_models.pack(side="left", padx=8)

        # One-click workload presets — fill sensible parameters across the
        # Benchmark, Soak and Provider-fit tabs.
        pf = ctk.CTkFrame(self.tab_conn, fg_color="transparent")
        pf.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(pf, text=self.L("Workload preset:")).pack(side="left")
        for key, label in (("Chat", "Chat"), ("RAG", "RAG (long context)"),
                           ("Agent", "Agent / batch")):
            ctk.CTkButton(pf, text=self.L(label), width=130,
                          command=lambda k=key: self._apply_preset(k)).pack(side="left", padx=(8, 0))

        sec2, body2 = self._section(self.tab_conn, "Server info")
        sec2.pack(fill="both", expand=True, padx=12, pady=12)
        self.conn_text = ctk.CTkTextbox(body2, wrap="word")
        self.conn_text.pack(fill="both", expand=True, padx=2, pady=2)
        self._log_conn("Enter a host/port and click 'Detect server', or use the "
                       "Network scan tab to find servers automatically.\n")

    def _build_bench_tab(self):
        sec, top = self._section(self.tab_bench, "Parameters")
        sec.pack(fill="x", padx=12, pady=(10, 6))

        self.var_tokens = tk.StringVar(value="256")
        self.var_ctx = tk.StringVar(value="2048")
        self.var_runs = tk.StringVar(value="3")
        self.var_conc = tk.StringVar(value="8")
        self.var_reqs = tk.StringVar(value="32")
        self.var_timeout = tk.StringVar(value="95")
        self.var_sweep = tk.StringVar(value="1,2,4,8,16")
        self.var_ctxprobe = tk.StringVar(value="16384")

        def field(r, c, label, var, info, w=80):
            self._lbl(top, label, info).grid(row=r, column=c, sticky="e", padx=(12, 4), pady=5)
            ctk.CTkEntry(top, textvariable=var, width=w).grid(row=r, column=c + 1, sticky="w", pady=5)

        field(0, 0, "Max output tokens", self.var_tokens, INFO["bench_tokens"])
        field(0, 2, "Context tokens", self.var_ctx, INFO["bench_ctx"])
        field(0, 4, "Timeout (s)", self.var_timeout, INFO["bench_timeout"])
        field(1, 0, "Throughput runs", self.var_runs, INFO["bench_runs"])
        field(1, 2, "Load concurrency", self.var_conc, INFO["bench_conc"])
        field(1, 4, "Load requests", self.var_reqs, INFO["bench_reqs"])
        field(2, 0, "Sweep concurrencies", self.var_sweep, INFO["bench_sweep"], w=130)
        field(2, 4, "Max ctx probe", self.var_ctxprobe, INFO["bench_ctxprobe"])

        sec2, tests = self._section(self.tab_bench, "Tests to run")
        sec2.pack(fill="x", padx=12, pady=(0, 6))
        self.t_speed = tk.BooleanVar(value=True)
        self.t_load = tk.BooleanVar(value=True)
        self.t_ctx = tk.BooleanVar(value=True)
        self.t_sanity = tk.BooleanVar(value=True)
        self.t_sweep = tk.BooleanVar(value=False)
        self.t_prefix = tk.BooleanVar(value=False)
        self.t_determ = tk.BooleanVar(value=False)
        self.t_limits = tk.BooleanVar(value=False)
        checks = [
            ("Speed (latency + throughput)", self.t_speed, INFO["t_speed"]),
            ("Load (parallel)", self.t_load, INFO["t_load"]),
            ("Context / prefill", self.t_ctx, INFO["t_ctx"]),
            ("Sanity (correctness)", self.t_sanity, INFO["t_sanity"]),
            ("Concurrency sweep", self.t_sweep, INFO["t_sweep"]),
            ("Prefix cache", self.t_prefix, INFO["t_prefix"]),
            ("Determinism", self.t_determ, INFO["t_determ"]),
            ("Limits + recall", self.t_limits, INFO["t_limits"]),
        ]
        for idx, (label, var, info) in enumerate(checks):
            fr = ctk.CTkFrame(tests, fg_color="transparent")
            fr.grid(row=idx // 4, column=idx % 4, sticky="w", padx=10, pady=5)
            ctk.CTkCheckBox(fr, text=label, variable=var).pack(side="left")
            self._info_icon(fr, label.split("(")[0].strip(), info).pack(side="left", padx=(4, 0))

        runbar = ctk.CTkFrame(self.tab_bench, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_run = ctk.CTkButton(runbar, text=self.L("Run benchmark"), command=self.on_run_bench)
        self.btn_run.pack(side="left")
        self.btn_repeat = ctk.CTkButton(runbar, text=self.L("Repeat last run"), state="disabled",
                                        command=self.on_repeat)
        self.btn_repeat.pack(side="left", padx=8)
        ctk.CTkButton(runbar, text=self.L("Export CSV…"), width=100,
                      command=self.export_bench).pack(side="left", padx=4)
        ctk.CTkButton(runbar, text=self.L("Copy to clipboard"), width=130,
                      command=self.copy_bench).pack(side="left", padx=4)
        self.btn_clear = ctk.CTkButton(runbar, text=self.L("Clear view"), fg_color="gray40",
                                       hover_color="gray30", command=self._clear_comparison)
        self.btn_clear.pack(side="left", padx=4)

        sec3, body = self._section(
            self.tab_bench,
            "Results — runs side by side (Latest = this run; scroll → for older)")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # Split: results table + chart on the left, live log on the right.
        # A ttk sash lets the user drag the divider to give either side more room.
        split = ttk.PanedWindow(body, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ctk.CTkFrame(split, fg_color="transparent")
        right = ctk.CTkFrame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)

        wrap, self.cmp_tree = self._tree_with_scrollbars(
            left, ("metric", "r0"), height=8, horizontal=True)
        wrap.pack(fill="both", expand=True)
        self.cmp_tree.tag_configure("header", background=self.pal["header_tag"][0],
                                    foreground=self.pal["header_tag"][1])
        self.cmp_tree.tag_configure("when", background=self.pal["when_tag"][0],
                                    foreground=self.pal["when_tag"][1])
        self.cmp_tree.bind("<<TreeviewSelect>>", self._on_cmp_select)
        self.bench_chart = ChartCanvas(left, height=150)
        self.bench_chart.pack(fill="x", pady=(8, 0))

        self.bench_log = LiveLog(right, self.pal, fg_color="transparent")
        self.bench_log.pack(fill="both", expand=True)

    # --------------------------------------------------------- Optimum finder
    def _build_opt_tab(self):
        self.var_opt_levels = tk.StringVar(value="1,2,4,8,16,24,32,48,64")
        self.var_opt_sizes = tk.StringVar(value="1024,2048,4096,8192,16384,32768,49152,65536")
        self.var_opt_basectx = tk.StringVar(value="1024")
        self.var_opt_gentok = tk.StringVar(value="64")
        self.var_opt_rpw = tk.StringVar(value="2")
        self.var_opt_ctxcap = tk.StringVar(value="65536")
        self.var_opt_minok = tk.StringVar(value="90")
        self.var_opt_frontier = tk.BooleanVar(value=True)
        self.var_opt_distinct = tk.BooleanVar(value=True)
        self.var_opt_gensweep = tk.BooleanVar(value=False)
        self.var_opt_gensizes = tk.StringVar(value="64,256,1024")
        self.var_opt_profiles = tk.BooleanVar(value=False)
        self.var_opt_proflist = tk.StringVar(value="8000/1000, 1000/8000, 1000/1000")
        self.var_opt_profconc = tk.StringVar(value="16")
        self.var_opt_settle = tk.StringVar(value="3")

        sec, top = self._section(self.tab_opt, "What to find")
        sec.pack(fill="x", padx=12, pady=(10, 6))

        def field(r, c, label, var, info, w=110):
            self._lbl(top, label, info).grid(row=r, column=c, sticky="e", padx=(12, 4), pady=5)
            ctk.CTkEntry(top, textvariable=var, width=w).grid(row=r, column=c + 1, sticky="w", pady=5)

        def check(r, cspan, text, var, info):
            fr = ctk.CTkFrame(top, fg_color="transparent")
            fr.grid(row=r, column=0, columnspan=cspan, sticky="w", padx=12, pady=(2, 4))
            ctk.CTkCheckBox(fr, text=text, variable=var).pack(side="left")
            self._info_icon(fr, text.split("(")[0].strip(), info).pack(side="left", padx=(5, 0))

        field(0, 0, "Concurrency levels", self.var_opt_levels, INFO["opt_levels"], w=200)
        field(0, 2, "Max context cap", self.var_opt_ctxcap, INFO["opt_ctxcap"])
        field(1, 0, "Concurrency-phase ctx", self.var_opt_basectx, INFO["opt_basectx"])
        field(1, 2, "Gen tokens / req", self.var_opt_gentok, INFO["opt_gentok"])
        field(2, 0, "Requests per worker", self.var_opt_rpw, INFO["opt_rpw"])
        field(2, 2, "Min success %", self.var_opt_minok, INFO["opt_minok"])
        field(2, 4, "Settle pause (s)", self.var_opt_settle, INFO["opt_settle"])
        check(3, 2, "Sweep request sizes (per-size concurrency)",
              self.var_opt_frontier, INFO["opt_frontier"])
        field(3, 2, "Request sizes (tok)", self.var_opt_sizes, INFO["opt_sizes"], w=200)
        check(4, 2, "Sweep generation lengths (at the recommended concurrency)",
              self.var_opt_gensweep, INFO["opt_gensweep"])
        field(4, 2, "Gen lengths (tok)", self.var_opt_gensizes, INFO["opt_gensizes"], w=200)
        check(5, 2, "Workload profiles (fixed in/out at set concurrency)",
              self.var_opt_profiles, INFO["opt_profiles"])
        field(5, 2, "Profiles in/out", self.var_opt_proflist, INFO["opt_proflist"], w=200)
        field(6, 2, "Profile concurrency", self.var_opt_profconc, INFO["opt_profconc"])
        check(6, 2, "Distinct request prefixes (avoid prefix-affinity pinning to one GPU)",
              self.var_opt_distinct, INFO["opt_distinct"])

        runbar = ctk.CTkFrame(self.tab_opt, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_opt = ctk.CTkButton(runbar, text=self.L("Find optima"), command=self.on_run_optima)
        self.btn_opt.pack(side="left")
        btn_opt_cancel = ctk.CTkButton(runbar, text=self.L("Cancel"), width=80, state="disabled",
                                       fg_color="#b04a4a", hover_color="#963c3c",
                                       command=self.cancel_current)
        btn_opt_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_opt_cancel)
        ctk.CTkButton(runbar, text=self.L("Export CSV…"), width=100,
                      command=self.export_optima).pack(side="left", padx=8)
        ctk.CTkButton(runbar, text=self.L("Copy to clipboard"), width=130,
                      command=self.copy_optima).pack(side="left", padx=4)
        ctk.CTkButton(runbar, text="Clear", width=80, fg_color="gray40", hover_color="gray30",
                      command=self._clear_opt).pack(side="left", padx=4)
        ctk.CTkLabel(runbar, text="Uses the current Connection host/model/timeout · "
                                  "bounded by early-stop + request timeout.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.opt_reco = ctk.CTkLabel(
            self.tab_opt, text="Recommendation will appear here after a run.",
            anchor="w", justify="left", font=ctk.CTkFont(weight="bold"))
        self.opt_reco.pack(fill="x", padx=16, pady=(0, 4))

        sec3, body = self._section(self.tab_opt, "Measured operating points")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        split = ttk.PanedWindow(body, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ctk.CTkFrame(split, fg_color="transparent")
        right = ctk.CTkFrame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)

        self._opt_cols = ("phase", "conc", "ctx", "gen", "reqs", "ok",
                          "in", "out", "total", "tpot", "p50", "p95", "ttft95", "verdict")
        widths = {"phase": 80, "conc": 55, "ctx": 85, "gen": 55, "reqs": 55, "ok": 65,
                  "in": 85, "out": 85, "total": 90, "tpot": 75, "p50": 75, "p95": 75,
                  "ttft95": 80, "verdict": 150}
        wrap, self.opt_tree = self._tree_with_scrollbars(
            left, self._opt_cols, height=12, horizontal=True, reorder=True)
        wrap.pack(fill="both", expand=True)
        heads = {"conc": "Conc", "ctx": "Ctx tok", "gen": "Gen tok", "reqs": "Reqs", "ok": "OK",
                 "in": "In tok/s", "out": "Out tok/s", "total": "Total tok/s",
                 "tpot": "TPOT ms", "p50": "Lat p50", "p95": "Lat p95", "ttft95": "TTFT p95"}
        for c in self._opt_cols:
            # Centre the numeric/short columns for readability; keep the free-text
            # verdict left-aligned (it holds long error messages).
            anchor = "w" if c == "verdict" else "center"
            self.opt_tree.heading(c, text=heads.get(c, c.capitalize()), anchor=anchor)
            self.opt_tree.column(c, width=widths[c], minwidth=48, anchor=anchor,
                                 stretch=(c == "verdict"))
        self.opt_tree.tag_configure("infeas", foreground=self.pal["live_err"])
        self.opt_tree.tag_configure("peak", background=self.pal["header_tag"][0],
                                    foreground=self.pal["header_tag"][1])
        self.opt_tree.tag_configure("knee", background=self.pal["when_tag"][0],
                                    foreground=self.pal["when_tag"][1])
        self.opt_chart = ChartCanvas(left, height=150)
        self.opt_chart.pack(fill="x", pady=(8, 0))

        self.opt_log = LiveLog(right, self.pal, fg_color="transparent")
        self.opt_log.pack(fill="both", expand=True)

    def _clear_opt(self):
        for i in self.opt_tree.get_children():
            self.opt_tree.delete(i)
        self._opt_iids = {}
        self._opt_points = []
        self.opt_chart.clear()
        self.opt_log.clear()
        self.opt_reco.configure(text="Recommendation will appear here after a run.")

    def on_run_optima(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
            levels = self._parse_levels(self.var_opt_levels.get(),
                                        [1, 2, 4, 8, 16, 24, 32, 48, 64])
            sizes = self._parse_levels(self.var_opt_sizes.get(),
                                       [1024, 2048, 4096, 8192, 16384, 32768, 49152, 65536])
            gen_sizes = self._parse_levels(self.var_opt_gensizes.get(), [64, 256, 1024])
            profiles = self._parse_profiles(self.var_opt_proflist.get())
            cfg = {
                "levels": levels,
                "sizes": sizes,
                "gen_sizes": gen_sizes,
                "profiles": profiles,
                "profile_conc": max(1, int(self.var_opt_profconc.get())),
                "base_ctx": max(1, int(self.var_opt_basectx.get())),
                "gen_tokens": max(1, int(self.var_opt_gentok.get())),
                "req_per_worker": max(1, int(self.var_opt_rpw.get())),
                "ctx_cap": max(256, int(self.var_opt_ctxcap.get())),
                "min_success": min(1.0, max(0.1, float(self.var_opt_minok.get()) / 100.0)),
                "do_frontier": bool(self.var_opt_frontier.get()),
                "do_gen_sweep": bool(self.var_opt_gensweep.get()),
                "do_profiles": bool(self.var_opt_profiles.get()),
                "settle_s": max(0.0, float(self.var_opt_settle.get() or 0)),
                "distinct_prefix": bool(self.var_opt_distinct.get()),
                "timeout": float(self.var_timeout.get() or 95),
            }
        except ValueError as e:
            return self._error(ValueError(f"Invalid number: {e}"))

        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=cfg["timeout"], endpoint=self.var_endpoint.get())
        self._remember_endpoint(target.host, target.port)
        self._clear_opt()
        self.opt_log.write(f"▶ Optimum finder · {client.base_url}", "head")
        self.opt_log.write(f"        levels: {levels}  ·  cap {cfg['ctx_cap']:,} tok", "dim")
        if cfg["do_frontier"]:
            self.opt_log.write(f"        sizes:  {sizes} tok", "dim")
        if cfg["do_gen_sweep"]:
            self.opt_log.write(f"        gen lengths: {gen_sizes} tok", "dim")
        if cfg["do_profiles"]:
            self.opt_log.write(f"        profiles (in/out): "
                               + ", ".join(f"{i}/{o}" for i, o in profiles)
                               + f"  @ c{cfg['profile_conc']}", "dim")
        self.opt_log.write(f"        settle pause: {cfg['settle_s']:g}s before each measurement", "dim")
        self.opt_log.write(
            "        distinct prefixes: "
            + ("ON — each request gets a unique preamble (spreads across backends)"
               if cfg["distinct_prefix"] else "OFF — requests may pin to one backend"),
            "dim")

        def on_status(msg):
            self.post(lambda m=msg: (self._set_status(m), self.opt_log.write(m, "head")))

        def on_point(p):
            self.post(lambda p=p: self._opt_add_point(p))

        self.run_async(
            B.find_optima(client, self._resolved_model(),
                          conc_levels=cfg["levels"], base_ctx=cfg["base_ctx"],
                          gen_tokens=cfg["gen_tokens"], req_per_worker=cfg["req_per_worker"],
                          min_success=cfg["min_success"], ctx_cap=cfg["ctx_cap"],
                          do_frontier=cfg["do_frontier"], sizes=cfg["sizes"],
                          distinct_prefix=cfg["distinct_prefix"],
                          do_gen_sweep=cfg["do_gen_sweep"], gen_sizes=cfg["gen_sizes"],
                          do_profiles=cfg["do_profiles"], profiles=cfg["profiles"],
                          profile_conc=cfg["profile_conc"], settle_s=cfg["settle_s"],
                          on_status=on_status, on_point=on_point),
            self._opt_done, status="Finding optima…")

    @staticmethod
    def _parse_profiles(spec: str) -> list:
        """Parse 'in/out, in/out' (e.g. '8000/1000, 1000/8000') into [(in, out), …]."""
        out = []
        for part in spec.split(","):
            part = part.strip()
            if "/" not in part:
                continue
            a, b = part.split("/", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                out.append((int(a), int(b)))
        return out

    def _opt_add_point(self, p):
        n = len(self._opt_points)
        self._opt_points.append(p)
        zebra = "even" if n % 2 else "odd"
        tags = (zebra,) if p.feasible else (zebra, "infeas")
        # TPOT is 0 only when the server didn't stream token-by-token (TTFT == total
        # latency) — show "–" rather than a misleading 0.000.
        tpot = f"{p.tpot_ms:.1f}" if p.tpot_ms > 0 else "–"
        if not p.feasible:
            verdict = "❌ " + (p.note or "failed")
        elif p.note:  # feasible but flagged (e.g. under-generation)
            verdict = "⚠ " + p.note
            tags = (tags[0], "infeas")  # reuse the warning colour
        elif p.est_frac >= 0.5:  # token counts guessed — tok/s only approximate
            verdict = "⚠ est tokens (no usage)"
            tags = (tags[0], "infeas")
        else:
            verdict = "✅ feasible"
        iid = self.opt_tree.insert("", "end", tags=tags, values=(
            p.phase, p.concurrency, f"{p.ctx_tokens:,}", p.gen_tokens, p.requests,
            f"{p.success}/{p.requests}", f"{p.input_tps:.0f}", f"{p.agg_tps:.0f}",
            f"{p.total_tps:.0f}", tpot, f"{p.lat_p50:.2f}", f"{p.lat_p95:.2f}",
            f"{p.ttft_p95:.3f}", verdict))
        self._opt_iids[id(p)] = iid
        self.opt_tree.see(iid)
        self.opt_log.result(f"{p.phase} c={p.concurrency} ctx={p.ctx_tokens:,} gen={p.gen_tokens}",
                            (f"in {p.input_tps:.0f} / out {p.agg_tps:.0f} tok/s · TPOT {tpot}"
                             f"{'ms' if p.tpot_ms > 0 else ''} · "
                             f"{p.req_per_s:.2f} req/s · {p.success}/{p.requests} ok") if p.feasible
                            else f"❌ {p.note or 'failed'}",
                            [], failed=not p.feasible)

    def _opt_done(self, summary):
        self._opt_summary = summary
        # Highlight the chosen operating points; apply peak last so it wins when
        # the peak and the knee are the same row.
        for key, tag in (("knee", "knee"), ("peak", "peak")):
            pt = summary.get(key)
            if pt is not None and id(pt) in self._opt_iids:
                iid = self._opt_iids[id(pt)]
                base = "even" if list(self.opt_tree.get_children()).index(iid) % 2 else "odd"
                self.opt_tree.item(iid, tags=(base, tag))

        lines = []
        mc, src = summary.get("max_ctx", 0), summary.get("max_ctx_source", "")
        lines.append(f"Max context: {mc:,} tokens ({src})" if mc else "Max context: unknown")
        knee, peak = summary.get("knee"), summary.get("peak")
        if peak:
            lines.append(f"Peak throughput: {peak.total_tps:.0f} tok/s total "
                         f"(in {peak.input_tps:.0f} / out {peak.agg_tps:.0f}) @ concurrency "
                         f"{peak.concurrency} (lat p95 {peak.lat_p95:.2f}s)")
        if knee:
            lines.append(f"Recommended concurrency: {knee.concurrency} — "
                         f"{knee.total_tps:.0f} tok/s total (in {knee.input_tps:.0f} / "
                         f"out {knee.agg_tps:.0f}) at lat p95 {knee.lat_p95:.2f}s (efficient knee)")
        if summary.get("max_feasible_c"):
            lines.append(f"Highest concurrency that held up: {summary['max_feasible_c']}")
        fr = summary.get("frontier") or []
        rows = [f for f in fr if f.get("max_c")]
        if rows:
            parts = [f"{f['ctx']:,}: best c{f.get('peak_c', 0)} "
                     f"({f.get('peak_tps', 0):.0f} tok/s), max c{f['max_c']}" for f in rows]
            lines.append("Per request size — " + "; ".join(parts))
        if summary.get("sizes_skipped"):
            sk = ", ".join(f"{s:,}" for s in summary["sizes_skipped"])
            lines.append(f"Sizes skipped (> max context): {sk}")
        gen_pts = [p for p in self._opt_points if p.phase == "gen" and p.feasible]
        if gen_pts:
            gc = summary.get("gen_sweep_conc") or (gen_pts[0].concurrency)
            parts = [f"{p.gen_tokens}→out {p.agg_tps:.0f} tok/s (p95 {p.lat_p95:.2f}s)" for p in gen_pts]
            lines.append(f"Per gen length @ c{gc} — " + "; ".join(parts))
        prof_pts = [p for p in self._opt_points if p.phase == "profile"]
        if prof_pts:
            pc = summary.get("profile_conc") or prof_pts[0].concurrency
            parts = [(f"{p.ctx_tokens}/{p.gen_tokens}→out {p.agg_tps:.0f} tok/s"
                      + (f", TPOT {p.tpot_ms:.1f}ms" if p.tpot_ms > 0 else "") if p.feasible
                      else f"{p.ctx_tokens}/{p.gen_tokens}→failed") for p in prof_pts]
            lines.append(f"Workload profiles @ c{pc} — " + "; ".join(parts))
        ok_pts = [p for p in self._opt_points if p.feasible and p.success > 0]
        if ok_pts and not any(p.tpot_ms > 0 for p in ok_pts):
            lines.append("TPOT n/a — the server did not stream tokens (TTFT = full latency), "
                         "likely a buffering gateway; per-token decode latency can't be measured")
        undergen = [p for p in ok_pts if p.note.startswith("under-gen")]
        if undergen:
            lines.append(f"⚠ {len(undergen)} point(s) under-generated (server ignored ignore_eos — "
                         "generated far fewer tokens than requested); their out/TPOT understate decode")
        estimated = [p for p in ok_pts if p.est_frac >= 0.5]
        if estimated:
            lines.append(f"⚠ {len(estimated)} point(s) had estimated token counts (server sent no "
                         "usage) — their in/out/total tok/s are approximate (~4 chars/token guess)")
        if summary.get("aborted"):
            lines.append(f"Note: {summary['aborted']}")
        reco = "  •  ".join(lines) if lines else "No optima found."
        self.opt_reco.configure(text=reco)
        self.opt_log.write("✓ Done — " + reco, "ok")
        self._set_status("Optimum finder complete.")
        self._plot_opt_curve()

    def _plot_opt_curve(self, metric="total tok/s"):
        pts = [p for p in self._opt_points if p.phase == "concurrency"]
        if not pts:
            return
        getter = {
            "total tok/s": lambda p: p.total_tps,
            "in tok/s": lambda p: p.input_tps,
            "out tok/s": lambda p: p.agg_tps,
            "latency p95": lambda p: p.lat_p95,
        }.get(metric, lambda p: p.total_tps)
        unit = "s" if metric.startswith("latency") else "tok/s"
        series = [(f"c{p.concurrency}", getter(p)) for p in sorted(pts, key=lambda p: p.concurrency)]
        self.opt_chart.plot(series, title=f"{metric} vs concurrency", unit=unit)

    _OPT_HEADER = ["phase", "concurrency", "ctx_tokens", "gen_tokens", "gen_actual", "requests",
                   "success", "in_tok_s", "out_tok_s", "total_tok_s", "tpot_ms", "req_per_s",
                   "peak_out_tok_s", "est_frac", "lat_p50_s", "lat_p95_s", "ttft_p95_s",
                   "feasible", "note"]

    def _opt_table(self) -> list[list]:
        """Header + one row per measured point (shared by CSV/clipboard export)."""
        rows = [list(self._OPT_HEADER)]
        for p in self._opt_points:
            rows.append([p.phase, p.concurrency, p.ctx_tokens, p.gen_tokens, f"{p.gen_actual:.0f}",
                         p.requests, p.success, f"{p.input_tps:.2f}", f"{p.agg_tps:.2f}",
                         f"{p.total_tps:.2f}", (f"{p.tpot_ms:.3f}" if p.tpot_ms > 0 else ""),
                         f"{p.req_per_s:.4f}", f"{p.peak_out_tps:.2f}", f"{p.est_frac:.2f}",
                         f"{p.lat_p50:.4f}", f"{p.lat_p95:.4f}", f"{p.ttft_p95:.4f}",
                         int(p.feasible), p.note])
        return rows

    def export_optima(self):
        if not self._opt_points:
            messagebox.showinfo(APP_TITLE, "No optimum-finder results to export yet — run it first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export optimum finder results", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(self._opt_table())
        self._set_status(f"Exported {len(self._opt_points)} operating point(s) to {path}")
        self.opt_log.write(f"💾 Exported {len(self._opt_points)} rows → {path}", "ok")

    def copy_optima(self):
        if not self._opt_points:
            messagebox.showinfo(APP_TITLE, "No optimum-finder results to copy yet — run it first.")
            return
        # Tab-separated so it pastes straight into a spreadsheet as columns; the
        # header row is included.
        text = "\n".join("\t".join(str(c) for c in row) for row in self._opt_table())
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status(f"Copied {len(self._opt_points)} operating point(s) to the clipboard.")
        self.opt_log.write(f"📋 Copied {len(self._opt_points)} rows to clipboard", "ok")

    # ------------------------------------------------------ tree export helpers
    @staticmethod
    def _tree_to_rows(tree) -> list[list]:
        """Snapshot a Treeview as a header row + one row per item (as displayed)."""
        cols = tree["columns"]
        rows = [[tree.heading(c)["text"] or str(c) for c in cols]]
        for iid in tree.get_children():
            rows.append([str(v) for v in tree.item(iid, "values")])
        return rows

    def export_bench(self):
        if not self.cmp_tree.get_children():
            messagebox.showinfo(APP_TITLE, "No benchmark results to export yet — run a benchmark first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export benchmark comparison", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = self._tree_to_rows(self.cmp_tree)
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        self._set_status(f"Exported benchmark comparison ({len(rows) - 1} rows) to {path}")
        self.bench_log.write(f"💾 Exported comparison ({len(rows) - 1} rows) → {path}", "ok")

    def copy_bench(self):
        if not self.cmp_tree.get_children():
            messagebox.showinfo(APP_TITLE, "No benchmark results to copy yet — run a benchmark first.")
            return
        rows = self._tree_to_rows(self.cmp_tree)
        text = "\n".join("\t".join(row) for row in rows)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status(f"Copied benchmark comparison ({len(rows) - 1} rows) to the clipboard.")
        self.bench_log.write(f"📋 Copied comparison ({len(rows) - 1} rows) to clipboard", "ok")

    # ------------------------------------------------------------------- Soak
    def _build_soak_tab(self):
        self.var_soak_conc = tk.StringVar(value="64")
        self.var_soak_in = tk.StringVar(value="4000")
        self.var_soak_out = tk.StringVar(value="500")
        self.var_soak_dur = tk.StringVar(value="30")
        self.var_soak_distinct = tk.BooleanVar(value=True)
        self.var_soak_overload = tk.BooleanVar(value=True)
        self.var_soak_theeye = tk.BooleanVar(value=False)

        sec, top = self._section(self.tab_soak, "Sustained throughput (tokens / hour)")
        sec.pack(fill="x", padx=12, pady=(10, 6))

        def field(r, c, label, var, info, w=90):
            self._lbl(top, label, info).grid(row=r, column=c, sticky="e", padx=(12, 4), pady=5)
            ctk.CTkEntry(top, textvariable=var, width=w).grid(row=r, column=c + 1, sticky="w", pady=5)

        field(0, 0, "Concurrency", self.var_soak_conc, INFO["soak_conc"])
        field(0, 2, "Duration (min)", self.var_soak_dur, INFO["soak_dur"])
        field(1, 0, "Input tokens / req", self.var_soak_in, INFO["soak_in"])
        field(1, 2, "Output tokens / req", self.var_soak_out, INFO["soak_out"])
        fr = ctk.CTkFrame(top, fg_color="transparent")
        fr.grid(row=2, column=0, columnspan=4, sticky="w", padx=12, pady=(2, 4))
        ctk.CTkCheckBox(fr, text=self.L("Distinct request prefixes (spread across backends)"),
                        variable=self.var_soak_distinct).pack(side="left")
        self._info_icon(fr, "Distinct request prefixes", INFO["soak_distinct"]).pack(side="left", padx=(5, 0))
        fr2 = ctk.CTkFrame(top, fg_color="transparent")
        fr2.grid(row=3, column=0, columnspan=4, sticky="w", padx=12, pady=(2, 4))
        ctk.CTkCheckBox(fr2, text="Overload probe (+10%) — push past the limit to check the "
                                  "server rejects the excess cleanly",
                        variable=self.var_soak_overload).pack(side="left")
        self._info_icon(fr2, "Overload probe", INFO["soak_overload"]).pack(side="left", padx=(5, 0))
        fr3 = ctk.CTkFrame(top, fg_color="transparent")
        fr3.grid(row=4, column=0, columnspan=4, sticky="w", padx=12, pady=(2, 4))
        ctk.CTkCheckBox(fr3, text="TheEye workload — replay the real production traffic mix "
                                  "(input/output fields ignored; only time & concurrency apply)",
                        variable=self.var_soak_theeye).pack(side="left")
        self._info_icon(fr3, "TheEye workload", INFO["soak_theeye"]).pack(side="left", padx=(5, 0))

        runbar = ctk.CTkFrame(self.tab_soak, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_soak = ctk.CTkButton(runbar, text=self.L("Run soak test"), command=self.on_run_soak)
        self.btn_soak.pack(side="left")
        btn_soak_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                        fg_color="#b04a4a", hover_color="#963c3c",
                                        command=self.cancel_current)
        btn_soak_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_soak_cancel)
        ctk.CTkLabel(runbar, text="Holds the load continuously and reports the sustained "
                                  "in/out token rate — raise the Timeout for large output sizes.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        # Big live readout
        self.soak_readout = ctk.CTkLabel(
            self.tab_soak, text=self.L("Set the load and press ‘Run soak test’."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.soak_readout.pack(fill="x", padx=16, pady=(2, 4))

        sec3, body = self._section(self.tab_soak, "Live")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        split = ttk.PanedWindow(body, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ctk.CTkFrame(split, fg_color="transparent")
        right = ctk.CTkFrame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)
        self.soak_chart = ChartCanvas(left, height=220)
        self.soak_chart.pack(fill="both", expand=True)
        self.soak_log = LiveLog(right, self.pal, fg_color="transparent")
        self.soak_log.pack(fill="both", expand=True)

    @staticmethod
    def _fmt_hms(sec: float) -> str:
        sec = int(max(0, sec))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    @staticmethod
    def _fmt_per_hour(v: float) -> str:
        if v >= 1e9:
            return f"{v / 1e9:.2f} B/h"
        if v >= 1e6:
            return f"{v / 1e6:.2f} M/h"
        if v >= 1e3:
            return f"{v / 1e3:.1f} K/h"
        return f"{v:.0f}/h"

    def on_run_soak(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
            base_conc = max(1, int(self.var_soak_conc.get()))
            overload = bool(self.var_soak_overload.get())
            # +10% concurrency (at least one extra request) to probe admission control.
            eff_conc = max(base_conc + 1, round(base_conc * 1.1)) if overload else base_conc
            theeye = bool(self.var_soak_theeye.get())
            cfg = {
                "base_conc": base_conc, "overload": overload, "concurrency": eff_conc,
                "theeye": theeye,
                "ctx_tokens": max(1, int(self.var_soak_in.get())),
                "gen_tokens": max(1, int(self.var_soak_out.get())),
                "duration_s": max(1.0, float(self.var_soak_dur.get()) * 60.0),
                "distinct_prefix": bool(self.var_soak_distinct.get()),
                "timeout": float(self.var_timeout.get() or 95),
            }
        except ValueError as e:
            return self._error(ValueError(f"Invalid number: {e}"))

        self._soak_target_out = cfg["gen_tokens"]
        self._soak_base_conc = cfg["base_conc"]
        self._soak_overload = cfg["overload"]
        self._soak_theeye = cfg["theeye"]
        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=cfg["timeout"], endpoint=self.var_endpoint.get())
        self._remember_endpoint(target.host, target.port)
        self.soak_chart.clear()
        self.soak_log.clear()
        mins = cfg["duration_s"] / 60.0
        cdesc = (f"c={cfg['concurrency']} (base {cfg['base_conc']} +10% overload probe)"
                 if cfg["overload"] else f"c={cfg['concurrency']}")
        wdesc = "TheEye workload mix" if cfg["theeye"] else f"in {cfg['ctx_tokens']} / out {cfg['gen_tokens']} tok"
        self.soak_log.write(f"▶ Soak · {client.base_url}", "head")
        self.soak_log.write(f"        {cdesc} · {wdesc} · {mins:g} min", "dim")
        self.soak_readout.configure(text=f"Starting… {cdesc}, {wdesc}, {mins:g} min")

        sampler = B.theeye_sample if cfg["theeye"] else None

        def on_progress(snap):
            self.post(lambda s=snap: self._soak_progress(s))

        self.run_async(
            B.soak_test(client, self._resolved_model(),
                        concurrency=cfg["concurrency"], ctx_tokens=cfg["ctx_tokens"],
                        gen_tokens=cfg["gen_tokens"], duration_s=cfg["duration_s"],
                        distinct_prefix=cfg["distinct_prefix"], sampler=sampler,
                        on_progress=on_progress),
            self._soak_done, status="Soak test running…")

    @staticmethod
    def _soak_verdict(s: dict) -> str:
        """Admission-control assessment for the overload probe."""
        degraded = s["undergen_frac"] >= 0.2 or s["est_frac"] >= 0.3
        if s["hard_err_frac"] >= 0.05:
            return ("❌ breaks under overload — hard errors/timeouts "
                    f"({s['hard_err_frac'] * 100:.0f}%), not clean rejections")
        if s["rejected_frac"] >= 0.02:
            return (f"✅ admission control OK — excess rejected cleanly (429/503) "
                    f"{s['rejected_frac'] * 100:.0f}% of requests")
        if degraded:
            return ("⚠ no admission control — accepted the overload and degraded "
                    "(truncated output) instead of rejecting")
        return "✓ absorbed +10% with no rejects and no degradation (headroom above the limit)"

    def _soak_readout_text(self, s: dict) -> str:
        warn = ""
        if s["undergen_frac"] >= 0.1:
            warn += f"  ⚠ under-gen {s['undergen_frac'] * 100:.0f}%"
        if s["est_frac"] >= 0.5:
            warn += "  ⚠ est-tokens"
        return (
            f"⏱ {self._fmt_hms(s['elapsed'])} / {self._fmt_hms(s['duration'])}   "
            f"({s['success']} ok · {s['rejected']} rejected(429) · {s['hard_err']} errored "
            f"· {s['req_per_s']:.1f} req/s)\n"
            f"IN    {s['in_tps']:>10,.0f} tok/s   →   {self._fmt_per_hour(s['in_per_hour'])}\n"
            f"OUT   {s['out_tps']:>10,.0f} tok/s   →   {self._fmt_per_hour(s['out_per_hour'])}\n"
            f"TOTAL {s['total_tps']:>10,.0f} tok/s   →   {self._fmt_per_hour(s['total_per_hour'])}"
            f"     · TPOT {s['tpot_ms']:.1f}ms · p95 {s['lat_p95']:.1f}s{warn}"
        )

    def _soak_progress(self, s: dict):
        self.soak_readout.configure(text=self._soak_readout_text(s))
        self._set_status(f"Soak: {self._fmt_hms(s['remaining'])} left · "
                         f"out {s['out_tps']:.0f} tok/s")
        if s.get("series"):
            self.soak_chart.plot([(f"{m}m", out) for m, out, _in in s["series"]],
                                 title="output tok/s per minute", unit="tok/s")

    def _soak_done(self, s: dict):
        self._soak_progress(s)
        self.soak_log.write("✓ Soak complete", "ok")
        self.soak_log.result(
            "Sustained throughput",
            f"IN {self._fmt_per_hour(s['in_per_hour'])} · OUT {self._fmt_per_hour(s['out_per_hour'])} · "
            f"TOTAL {self._fmt_per_hour(s['total_per_hour'])}",
            [("input tok/s", f"{s['in_tps']:.0f}"),
             ("output tok/s", f"{s['out_tps']:.0f}"),
             ("total tok/s", f"{s['total_tps']:.0f}"),
             ("in tokens (run)", f"{s['in_tokens']:,}"),
             ("out tokens (run)", f"{s['out_tokens']:,}"),
             ("requests", f"{s['success']} ok / {s['errors']} failed"),
             ("req/s", f"{s['req_per_s']:.2f}"),
             ("TPOT (ms)", f"{s['tpot_ms']:.1f}" if s['tpot_ms'] > 0 else "–"),
             ("latency p50 / p95 (s)", f"{s['lat_p50']:.2f} / {s['lat_p95']:.2f}"),
             ("mean in/out tok/req",
              f"{(s['in_tokens'] / s['success']) if s['success'] else 0:.0f} / {s['gen_actual']:.0f}"
              + (f" (requested {s['req_out_mean']:.0f})" if getattr(self, '_soak_theeye', False)
                 else f" / {getattr(self, '_soak_target_out', '?')}")),
             ("under-gen requests", f"{s['undergen_frac'] * 100:.1f}%"),
             ("rejected (429/503)", f"{s['rejected']} ({s['rejected_frac'] * 100:.1f}%)"),
             ("hard errors", f"{s['hard_err']} ({s['hard_err_frac'] * 100:.1f}%)"),
             ("est tokens", f"{s['est_frac'] * 100:.0f}%")])
        if getattr(self, "_soak_overload", False):
            verdict = self._soak_verdict(s)
            base = getattr(self, "_soak_base_conc", "?")
            self.soak_log.write(f"Overload probe (base {base} +10% → c={s['concurrency']}): {verdict}",
                                "ok" if verdict.startswith(("✅", "✓")) else "err")
        for e in s.get("error_samples", []):
            self.soak_log.write(f"   error: {e[:70]}", "err")
        self._set_status("Soak test complete.")

    # ---------------------------------------------------------- Capacity tab
    def _build_capacity_tab(self):
        self.var_cap_maxconc = tk.StringVar(value="64")
        self.var_cap_in = tk.StringVar(value="1000")
        self.var_cap_out = tk.StringVar(value="500")
        self.var_cap_window = tk.StringVar(value="40")
        self.var_cap_target = tk.StringVar(value="")

        sec, top = self._section(self.tab_capacity, "Token capacity (peak tokens / minute)")
        sec.pack(fill="x", padx=12, pady=(10, 6))

        def field(r, c, label, var, info, w=90):
            self._lbl(top, label, info).grid(row=r, column=c, sticky="e", padx=(12, 4), pady=5)
            ctk.CTkEntry(top, textvariable=var, width=w).grid(row=r, column=c + 1, sticky="w", pady=5)

        field(0, 0, "Max concurrency", self.var_cap_maxconc, INFO["cap_max_conc"])
        field(0, 2, "Window / step (s)", self.var_cap_window, INFO["cap_window"])
        field(1, 0, "Input tokens / req", self.var_cap_in, INFO["cap_in"])
        field(1, 2, "Output tokens / req", self.var_cap_out, INFO["cap_out"])
        field(2, 0, "Target tok/min (optional)", self.var_cap_target, INFO["cap_target"], w=110)

        runbar = ctk.CTkFrame(self.tab_capacity, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_capacity = ctk.CTkButton(runbar, text=self.L("Run capacity test"),
                                          command=self.on_run_capacity)
        self.btn_capacity.pack(side="left")
        btn_cap_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                       fg_color="#b04a4a", hover_color="#963c3c",
                                       command=self.cancel_current)
        btn_cap_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_cap_cancel)
        ctk.CTkLabel(runbar, text="Ramps concurrency (1→2→4→…) and reports the peak "
                                  "sustainable tok/min — the endpoint's capacity ceiling.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.capacity_readout = ctk.CTkLabel(
            self.tab_capacity, text=self.L("Set the load and press ‘Run capacity test’."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.capacity_readout.pack(fill="x", padx=16, pady=(2, 4))
        # Default label colour, restored on each run before a verdict recolours it.
        self._cap_readout_color = self.capacity_readout.cget("text_color")

        sec3, body = self._section(self.tab_capacity, "Live")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        split = ttk.PanedWindow(body, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ctk.CTkFrame(split, fg_color="transparent")
        right = ctk.CTkFrame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)
        self.capacity_chart = ChartCanvas(left, height=220)
        self.capacity_chart.pack(fill="both", expand=True)
        self.capacity_log = LiveLog(right, self.pal, fg_color="transparent")
        self.capacity_log.pack(fill="both", expand=True)

    @staticmethod
    def _fmt_per_min(v: float) -> str:
        if v >= 1e9:
            return f"{v / 1e9:.2f} B/min"
        if v >= 1e6:
            return f"{v / 1e6:.2f} M/min"
        if v >= 1e3:
            return f"{v / 1e3:.1f} K/min"
        return f"{v:.0f}/min"

    def on_run_capacity(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
            tgt_raw = self.var_cap_target.get().strip()
            cfg = {
                "max_conc": max(1, int(self.var_cap_maxconc.get())),
                "ctx_tokens": max(1, int(self.var_cap_in.get())),
                "gen_tokens": max(1, int(self.var_cap_out.get())),
                "window_s": max(3.0, float(self.var_cap_window.get())),
                "target_per_min": max(0.0, float(tgt_raw)) if tgt_raw else 0.0,
                "timeout": float(self.var_timeout.get() or 95),
            }
        except ValueError as e:
            return self._error(ValueError(f"Invalid number: {e}"))

        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=cfg["timeout"], endpoint=self.var_endpoint.get())
        self._remember_endpoint(target.host, target.port)
        self.capacity_chart.clear()
        self.capacity_log.clear()
        ramp = B._capacity_levels(cfg["max_conc"])
        tdesc = (f" · target {self._fmt_per_min(cfg['target_per_min'])}"
                 if cfg["target_per_min"] else "")
        self.capacity_log.write(f"▶ Capacity · {client.base_url}", "head")
        self.capacity_log.write(
            f"        ramp c={'→'.join(str(c) for c in ramp)} · in {cfg['ctx_tokens']} / "
            f"out {cfg['gen_tokens']} tok · {cfg['window_s']:g}s/step{tdesc}", "dim")
        self.capacity_readout.configure(text="Starting capacity ramp…",
                                        text_color=self._cap_readout_color)

        def on_progress(snap):
            self.post(lambda s=snap: self._capacity_progress(s))

        self.run_async(
            B.capacity_test(client, self._resolved_model(),
                            max_conc=cfg["max_conc"], ctx_tokens=cfg["ctx_tokens"],
                            gen_tokens=cfg["gen_tokens"], window_s=cfg["window_s"],
                            target_per_min=cfg["target_per_min"], on_progress=on_progress),
            self._capacity_done, status="Capacity test running…")

    def _capacity_plot(self, steps: list):
        if not steps:
            return
        # Saturated levels red; ring the best healthy level as the peak.
        healthy = [x for x in steps if x.get("healthy")]
        peak = max(healthy, key=lambda x: x["total_per_min"]) if healthy else None
        mark = steps.index(peak) if peak else None
        self.capacity_chart.plot(
            [(f"c{x['conc']}", x["total_per_min"],
              None if x.get("healthy") else "err") for x in steps],
            title="total tok/min vs concurrency  (red = past capacity)",
            unit="tok/min", mark=mark,
            mark_text=f"peak {ChartCanvas._fmt_val(peak['total_per_min'])}" if peak else "")

    def _capacity_progress(self, s: dict):
        phase = s.get("phase")
        steps = s.get("steps", [])
        if phase == "step_start":
            self.capacity_readout.configure(
                text=f"⏱ measuring c={s.get('conc')} …   "
                     + (f"best so far {self._fmt_per_min(s['peak']['total_per_min'])} "
                        f"@ c={s['peak']['conc']}" if s.get("peak") else ""))
            self._set_status(s.get("status", "Capacity test running…"))
            return
        # step_done (or done): a level finished — log it and redraw the curve.
        cur = s.get("current")
        if cur:
            tag = "ok" if cur["healthy"] else "err"
            flags = "" if cur["healthy"] else "  ⚠"
            if cur["rejected"]:
                flags += f" · {cur['rejected']} rejected(429)"
            if cur["hard_err"]:
                flags += f" · {cur['hard_err']} errored"
            if cur["undergen_frac"] >= 0.1:
                flags += f" · under-gen {cur['undergen_frac'] * 100:.0f}%"
            self.capacity_log.write(
                f"c={cur['conc']:>3}  {self._fmt_per_min(cur['total_per_min']):>12}  "
                f"out {self._fmt_per_min(cur['out_per_min']):>12}  "
                f"p95 {cur['lat_p95']:5.1f}s{flags}",
                tag)
        self._capacity_plot(steps)
        peak = s.get("peak")
        if peak:
            self.capacity_readout.configure(
                text=f"Peak so far: {self._fmt_per_min(peak['total_per_min'])} @ c={peak['conc']}   "
                     f"(out {self._fmt_per_min(peak['out_per_min'])} · "
                     f"in {self._fmt_per_min(peak['in_per_min'])})")
        self._set_status(s.get("status", "Capacity test running…"))

    def _capacity_done(self, s: dict):
        GREEN, RED = ("#1c8a44", "#57c07a"), ("#b23b3b", "#e26d6d")
        self._capacity_plot(s.get("steps", []))
        peak_pm = s.get("peak_total_per_min", 0.0)
        self.capacity_log.write("✓ Capacity ramp complete", "ok")
        if not s.get("peak"):
            # Not one level held up (server rejected/failed from the very start),
            # so there is no sustainable capacity number to report.
            self.capacity_readout.configure(
                text="NO SUSTAINABLE CAPACITY — the endpoint rejected or failed "
                     "from the lowest concurrency level.", text_color=RED)
            self.capacity_log.write(f"◆ {s.get('saturation', '')}", "err")
            if s.get("target_per_min"):
                self.capacity_log.write(
                    f"❌ FAIL — target {self._fmt_per_min(s['target_per_min'])}: "
                    f"no capacity could be sustained", "err")
            self._set_status("Capacity test complete — no sustainable capacity.")
            return
        tgt = s.get("target_per_min") or 0.0
        met = bool(s.get("target_met"))
        tdesc = ""
        if tgt > 0:
            tdesc = (f"   ·   {'✅ target met' if met else '❌ below target'} "
                     f"({self._fmt_per_min(tgt)})")
        self.capacity_readout.configure(
            text=f"CAPACITY  {self._fmt_per_min(peak_pm)}  @ c={s.get('peak_conc')}   "
                 f"(out {self._fmt_per_min(s.get('peak_out_per_min', 0))} · "
                 f"in {self._fmt_per_min(s.get('peak_in_per_min', 0))}){tdesc}",
            text_color=RED if (tgt > 0 and not met) else GREEN)
        rows = [
            ("peak total tok/min", f"{peak_pm:,.0f}"),
            ("peak output tok/min", f"{s.get('peak_out_per_min', 0):,.0f}"),
            ("peak input tok/min", f"{s.get('peak_in_per_min', 0):,.0f}"),
            ("at concurrency", f"{s.get('peak_conc')}"),
            ("tokens / hour (total)", self._fmt_per_hour(peak_pm * 60.0)),
            ("levels tested", "→".join(str(c) for c in s.get("ramp", []))),
            ("window / step (s)", f"{s.get('window_s', 0):g}"),
            ("saturation", s.get("saturation", "")),
        ]
        self.capacity_log.result(
            "Token capacity",
            f"{self._fmt_per_min(peak_pm)} @ c={s.get('peak_conc')}", rows)
        self.capacity_log.write(f"◆ Saturation: {s.get('saturation', '')}", "dim")
        if tgt > 0:
            self.capacity_log.write(
                f"{'✅ PASS' if met else '❌ FAIL'} — target {self._fmt_per_min(tgt)}: "
                f"measured peak {self._fmt_per_min(peak_pm)} "
                f"({'meets' if met else 'below'} required capacity)",
                "ok" if met else "err")
        for st in s.get("steps", []):
            for e in st.get("error_samples", []):
                self.capacity_log.write(f"   c={st['conc']} error: {e[:64]}", "err")
                break
        self._set_status("Capacity test complete.")

    # ---------------------------------------------------------- Model fit tab
    def _build_modelfit_tab(self):
        self.var_fit_tool = tk.BooleanVar(value=True)
        self.var_fit_json = tk.BooleanVar(value=True)
        self.var_fit_instruct = tk.BooleanVar(value=True)
        self.var_fit_latency = tk.BooleanVar(value=True)
        self.var_fit_nothink = tk.BooleanVar(value=True)

        sec, top = self._section(self.tab_fit, "Model fit — agentic suitability")
        sec.pack(fill="x", padx=12, pady=(10, 6))

        intro = ctk.CTkLabel(
            top, anchor="w", justify="left", text_color=self.pal["sub"],
            text=("Runs a battery of capability probes and grades whether the model is fit "
                  "for agentic use (native tool-calling, with a Hermes-prompt fallback). "
                  "Verdict: SOBIB / PIIRIPEAL / EI SOBI."))
        intro.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(4, 6))

        def check(r, label, var, info):
            fr = ctk.CTkFrame(top, fg_color="transparent")
            fr.grid(row=r, column=0, columnspan=4, sticky="w", padx=12, pady=2)
            ctk.CTkCheckBox(fr, text=self.L(label), variable=var).pack(side="left")
            self._info_icon(fr, label, info).pack(side="left", padx=(5, 0))

        check(1, "Tool-calling (native OpenAI tools API, Hermes-prompt fallback)",
              self.var_fit_tool, INFO["fit_tool"])
        check(2, "Structured JSON output (strict, parseable, correct schema)",
              self.var_fit_json, INFO["fit_json"])
        check(3, "Instruction following & format discipline (no leaked reasoning)",
              self.var_fit_instruct, INFO["fit_instruct"])
        check(4, "Latency & throughput on these prompts",
              self.var_fit_latency, INFO["fit_latency"])
        check(5, "Disable thinking during test (test the agentic mode)",
              self.var_fit_nothink, INFO["rd_nothink"])

        runbar = ctk.CTkFrame(self.tab_fit, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_fit = ctk.CTkButton(runbar, text=self.L("Run model-fit test"), command=self.on_run_modelfit)
        self.btn_fit.pack(side="left")
        btn_fit_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                       fg_color="#b04a4a", hover_color="#963c3c",
                                       command=self.cancel_current)
        btn_fit_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_fit_cancel)
        ctk.CTkButton(runbar, text=self.L("Copy results"), width=110,
                      command=self.copy_modelfit_results).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(runbar, text="Deterministic (temperature 0) — a couple dozen short probes.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.fit_readout = ctk.CTkLabel(
            self.tab_fit, text=self.L("Pick the checks and press ‘Run model-fit test’."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.fit_readout.pack(fill="x", padx=16, pady=(2, 4))

        sec3, body = self._section(self.tab_fit, "Live")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        split = ttk.PanedWindow(body, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ctk.CTkFrame(split, fg_color="transparent")
        right = ctk.CTkFrame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)
        cols = ("dim", "case", "result", "detail")
        wrap, self.fit_tree = self._tree_with_scrollbars(left, cols, height=14, horizontal=True)
        for c, w, txt in (("dim", 80, "dimension"), ("case", 300, "probe"),
                          ("result", 60, "ok"), ("detail", 260, "detail")):
            self.fit_tree.heading(c, text=txt)
            self.fit_tree.column(c, width=w, anchor="w")
        self.fit_tree.tag_configure("pass", foreground=self.pal["live_ok"])
        self.fit_tree.tag_configure("fail", foreground=self.pal["live_err"])
        wrap.pack(fill="both", expand=True)
        # Double-click a row to see the full probe + detail (the columns truncate).
        self._fit_case_by_iid = {}
        self.fit_tree.bind("<Double-1>", self._on_fit_row_open)
        ctk.CTkLabel(left, text="Double-click a row to see the full probe & detail.",
                     text_color=self.pal["sub"]).pack(anchor="w", pady=(2, 0))
        self.fit_log = LiveLog(right, self.pal, fg_color="transparent")
        self.fit_log.pack(fill="both", expand=True)

    def on_run_modelfit(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
        except ValueError as e:
            return self._error(ValueError(f"Invalid host: {e}"))
        dims = [d for d, v in (("tool", self.var_fit_tool), ("json", self.var_fit_json),
                               ("instruct", self.var_fit_instruct),
                               ("latency", self.var_fit_latency)) if v.get()]
        if not dims or dims == ["latency"]:
            return self._error(ValueError("Select at least one capability dimension to test."))
        no_think = bool(self.var_fit_nothink.get())
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}} if no_think else None
        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=float(self.var_timeout.get() or 95), endpoint=self.var_endpoint.get(),
            extra_body=extra_body)
        self._remember_endpoint(target.host, target.port)
        # Stash connection + params so _modelfit_done can persist the result to
        # History and show the run-over-run comparison, like the Benchmark tab.
        self._fit_conn = (client.host, client.port, client.endpoint)
        self._fit_params = {"dims": dims}
        for iid in self.fit_tree.get_children():
            self.fit_tree.delete(iid)
        self._fit_case_by_iid = {}
        self.fit_log.clear()
        self.fit_log.write(f"▶ Model fit · {client.base_url}", "head")
        thinkdesc = "thinking OFF (agentic mode)" if no_think else "thinking as-configured"
        self.fit_log.write(f"        dimensions: {', '.join(dims)} · {thinkdesc}", "dim")
        self.fit_readout.configure(text="Running probes…")

        def on_progress(evt):
            self.post(lambda e=evt: self._modelfit_progress(e))

        self.run_async(
            B.suitability_test(client, self._resolved_model(), dims=dims,
                               on_progress=on_progress),
            self._modelfit_done, status="Model-fit test running…")

    _DIM_LABEL = {"tool": "tool-call", "json": "json", "instruct": "instruct"}

    def _modelfit_progress(self, evt: dict):
        ev = evt.get("event")
        if ev == "case":
            dim = self._DIM_LABEL.get(evt["dim"], evt["dim"])
            mark = "✓" if evt["ok"] else "✗"
            iid = self.fit_tree.insert("", "end", tags=("pass" if evt["ok"] else "fail",),
                                       values=(dim, evt["user"][:70], mark, evt["detail"][:80]))
            # Keep the full, untruncated text for the double-click detail view.
            self._fit_case_by_iid[iid] = {"dim": dim, "user": evt["user"],
                                          "ok": evt["ok"], "detail": evt["detail"]}
            self.fit_tree.yview_moveto(1.0)
        elif ev == "dim_done":
            self.fit_log.write(f"   {evt['dim']}: score {evt['score'] * 100:.0f}%",
                               "ok" if evt["score"] >= 0.85 else "err")

    def copy_modelfit_results(self):
        """Copy the model-fit report (verdict + scores) plus the full per-probe
        table (with untruncated details) to the clipboard."""
        report = self.fit_log.get_text().strip()
        rows = self.fit_tree.get_children()
        if not report and not rows:
            messagebox.showinfo(APP_TITLE, "No model-fit results to copy yet — run a test first.")
            return
        parts = []
        if report:
            parts.append(report)
        if rows:
            parts.append("")
            parts.append("dimension\tprobe\tok\tdetail")
            for iid in rows:
                d = self._fit_case_by_iid.get(iid)
                if d:
                    parts.append(f"{d['dim']}\t{d['user']}\t"
                                 f"{'ok' if d['ok'] else 'fail'}\t{d['detail']}")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(parts))
        self._set_status(f"Copied model-fit results ({len(rows)} probes) to the clipboard.")
        self.fit_log.write(f"📋 Copied results ({len(rows)} probes) to clipboard", "ok")

    def _on_fit_row_open(self, event):
        """Double-click a Model-fit row → show its full probe & detail."""
        iid = self.fit_tree.identify_row(event.y)
        d = self._fit_case_by_iid.get(iid)
        if not d:
            return
        self._show_detail_dialog(
            "Model-fit probe",
            [f"Dimension:  {d['dim']}",
             f"Result:     {'✓ pass' if d['ok'] else '✗ fail'}",
             "", "Probe:", d["user"], "", "Detail:", d["detail"]])

    def _show_detail_dialog(self, title: str, lines: list):
        """A small read-only, copyable popup showing full text for a table row."""
        win = ctk.CTkToplevel(self.root)
        win.title(title)
        win.geometry(self._sized_geometry(680, 420))
        win.transient(self.root)
        box = ctk.CTkTextbox(win, wrap="word", font=("TkFixedFont", 13))
        box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")
        ctk.CTkButton(win, text=self.t("close"), width=90,
                      command=win.destroy).pack(pady=(0, 12))

    def _modelfit_done(self, report: dict):
        v = report["verdict"]
        colour = (self.pal["live_ok"] if v.startswith("✅")
                  else "#d0902a" if v.startswith("⚠") else self.pal["live_err"])
        self.fit_readout.configure(text=f"{v}   (overall {report['overall'] * 100:.0f}%)",
                                   text_color=colour)
        self.fit_log.write("✓ Model-fit test complete", "ok")

        rows = []
        sc = report.get("scores", {})
        if "tool" in report:
            t = report["tool"]
            rows.append(("Tool-calling", f"{sc.get('tool', 0) * 100:.0f}%"))
            rows.append(("  valid tool call", f"{t['valid_rate'] * 100:.0f}%"))
            rows.append(("  correct tool", f"{t['select_rate'] * 100:.0f}%"))
            rows.append(("  correct arguments", f"{t['arg_rate'] * 100:.0f}%"))
            rows.append(("  spurious calls (should be 0)", f"{t['falsecall_rate'] * 100:.0f}%"))
        if "json" in report:
            j = report["json"]
            rows.append(("Structured JSON", f"{sc.get('json', 0) * 100:.0f}%"))
            rows.append(("  parses / correct schema",
                         f"{j['parse_rate'] * 100:.0f}% / {j['schema_rate'] * 100:.0f}%"))
        if "instruct" in report:
            i = report["instruct"]
            rows.append(("Instruction following", f"{sc.get('instruct', 0) * 100:.0f}%"))
            rows.append(("  format followed / reasoning leaked",
                         f"{i['follow_rate'] * 100:.0f}% / {i['leak_rate'] * 100:.0f}%"))
        if "latency" in report:
            l = report["latency"]
            rows.append(("Latency mean / p95",
                         f"{l['mean_s']:.2f}s / {l['p95_s']:.2f}s · {l['mean_out_tps']:.0f} tok/s"))
        self.fit_log.result("Suitability", v, rows, failed=not v.startswith("✅"))
        # Persist to History + show the run-over-run comparison (overall fit %),
        # mirroring how the Benchmark tab records each result.
        conn = getattr(self, "_fit_conn", None)
        if conn:
            host, port, endpoint = conn
            params = getattr(self, "_fit_params", {"dims": report.get("dims", [])})
            self._record_and_compare(
                host, port, report.get("model", ""), endpoint, "model-fit",
                params, v, rows,
                value=report.get("overall", 0.0) * 100, value_label="fit %")
        self._set_status("Model-fit test complete.")

    # ------------------------------------------------------ Provider fit tab
    def _build_readiness_tab(self):
        self.var_rd_in = tk.StringVar(value="1024")
        self.var_rd_out = tk.StringVar(value="256")
        self.var_rd_sweep = tk.StringVar(value="1,4,8,16,32")
        self.var_rd_reqs = tk.StringVar(value="16")
        self.var_rd_sla = tk.StringVar(value="3")
        self.var_rd_ctx = tk.StringVar(value="8192")
        self.var_rd_overload = tk.BooleanVar(value=True)
        self.var_rd_distinct = tk.BooleanVar(value=True)
        self.var_rd_integrity = tk.BooleanVar(value=True)
        self.var_rd_nothink = tk.BooleanVar(value=True)

        sec, top = self._section(self.tab_ready,
                                 "Provider fit — OpenRouter / HuggingFace readiness")
        sec.pack(fill="x", padx=12, pady=(10, 6))

        # Two aligned label/entry pairs per row, then a stretchy tail column that
        # absorbs slack so the fields stay compact on the left (without it, the
        # long intro inflates the grid and scatters the right-hand pair off-screen).
        for col, ms in ((0, 150), (1, 140), (2, 30), (3, 150), (4, 140)):
            top.grid_columnconfigure(col, minsize=ms)
        top.grid_columnconfigure(5, weight=1)

        intro = ctk.CTkLabel(
            top, anchor="w", justify="left", text_color=self.pal["sub"], wraplength=760,
            text=("Checks the API contract routers require (streaming, usage accounting, "
                  "max_tokens/stop, deterministic decode, sampling params, clean errors), "
                  "then sweeps concurrency to find the throughput knee and the first "
                  "bottleneck. Verdict: SOBIB / PIIRIPEAL / EI SOBI per provider."))
        intro.grid(row=0, column=0, columnspan=6, sticky="ew", padx=12, pady=(4, 8))

        # Keep the intro wrapping to the actual pane width as the window resizes
        # (guarded so setting wraplength doesn't loop on its own re-layout).
        def _rewrap(e, lbl=intro):
            want = max(320, e.width - 28)
            if abs(lbl.cget("wraplength") - want) > 12:
                lbl.configure(wraplength=want)
        top.bind("<Configure>", _rewrap)

        def field(r, pair, label, var, info, w=130):
            col = 0 if pair == 0 else 3
            self._lbl(top, label, info).grid(row=r, column=col, sticky="e", padx=(12, 6), pady=6)
            ctk.CTkEntry(top, textvariable=var, width=w).grid(
                row=r, column=col + 1, sticky="w", pady=6)

        field(1, 0, "Input tokens / req", self.var_rd_in, INFO["rd_in"])
        field(1, 1, "Output tokens / req", self.var_rd_out, INFO["rd_out"])
        field(2, 0, "Concurrency sweep", self.var_rd_sweep, INFO["rd_sweep"], w=140)
        field(2, 1, "Requests / level", self.var_rd_reqs, INFO["rd_reqs"])
        field(3, 0, "TTFT p95 SLA (s)", self.var_rd_sla, INFO["rd_sla"])
        field(3, 1, "Context probe (tok)", self.var_rd_ctx, INFO["rd_ctx"])

        ctk.CTkLabel(top, text=self.L("Checks"), anchor="w", text_color=self.pal["sub"],
                     font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=4, column=0, columnspan=6, sticky="w", padx=12, pady=(10, 2))

        def check(r, pair, label, var, info, title):
            col = 0 if pair == 0 else 3
            fr = ctk.CTkFrame(top, fg_color="transparent")
            fr.grid(row=r, column=col, columnspan=3, sticky="w", padx=12, pady=(2, 4))
            ctk.CTkCheckBox(fr, text=self.L(label), variable=var).pack(side="left")
            self._info_icon(fr, title, info).pack(side="left", padx=(5, 0))

        check(5, 0, "Integrity probes — token-count honesty, context recall, model quality",
              self.var_rd_integrity, INFO["rd_integrity"], "Integrity probes")
        check(6, 0, "Overload probe (+25%) — check clean admission control",
              self.var_rd_overload, INFO["rd_overload"], "Overload probe")
        check(7, 0, "Distinct request prefixes (spread across backends)",
              self.var_rd_distinct, INFO["rd_distinct"], "Distinct request prefixes")
        check(8, 0, "Disable thinking during test (test the agentic mode)",
              self.var_rd_nothink, INFO["rd_nothink"], "Disable thinking")

        runbar = ctk.CTkFrame(self.tab_ready, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_ready = ctk.CTkButton(runbar, text=self.L("Run provider-fit test"),
                                       command=self.on_run_readiness)
        self.btn_ready.pack(side="left")
        btn_ready_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                         fg_color="#b04a4a", hover_color="#963c3c",
                                         command=self.cancel_current)
        btn_ready_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_ready_cancel)
        ctk.CTkButton(runbar, text=self.L("Copy sweep table"), width=140,
                      command=self.copy_readiness_table).pack(side="left", padx=(0, 4))
        ctk.CTkButton(runbar, text=self.L("Copy report"), width=110,
                      command=self.copy_readiness_report).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(runbar, text="Compliance probes then a concurrency sweep — raise the "
                                  "Timeout for large output sizes.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.ready_readout = ctk.CTkLabel(
            self.tab_ready, text=self.L("Set the traffic shape and press ‘Run provider-fit test’."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.ready_readout.pack(fill="x", padx=16, pady=(2, 4))

        sec3, body = self._section(self.tab_ready, "Live")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        split = ttk.PanedWindow(body, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ctk.CTkFrame(split, fg_color="transparent")
        right = ctk.CTkFrame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)
        self.ready_chart = ChartCanvas(left, height=170)
        self.ready_chart.pack(fill="x")
        cols = ("conc", "out_tps", "ttft", "tpot", "reqs", "rej", "err")
        wrap, self.ready_tree = self._tree_with_scrollbars(left, cols, height=8, horizontal=True)
        for c, w, txt in (("conc", 60, "conc"), ("out_tps", 90, "out tok/s"),
                          ("ttft", 90, "TTFT p95"), ("tpot", 80, "TPOT ms"),
                          ("reqs", 80, "ok/req"), ("rej", 70, "429/503"),
                          ("err", 70, "hard err")):
            self.ready_tree.heading(c, text=txt)
            self.ready_tree.column(c, width=w, anchor="w")
        self.ready_tree.tag_configure("ovl", foreground=self.pal["warn"])
        wrap.pack(fill="both", expand=True, pady=(6, 0))
        self.ready_log = LiveLog(right, self.pal, fg_color="transparent")
        self.ready_log.pack(fill="both", expand=True)

    def on_run_readiness(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
            levels = [int(x) for x in self.var_rd_sweep.get().replace(" ", "").split(",") if x]
            levels = sorted({x for x in levels if x >= 1})
            if not levels:
                return self._error(ValueError("Enter at least one concurrency level (e.g. 1,4,8,16)."))
            cfg = {
                "in_tokens": max(1, int(self.var_rd_in.get())),
                "out_tokens": max(1, int(self.var_rd_out.get())),
                "levels": levels,
                "reqs_per_level": max(1, int(self.var_rd_reqs.get())),
                "ttft_sla_s": max(0.1, float(self.var_rd_sla.get())),
                "ctx_probe_tokens": max(256, int(self.var_rd_ctx.get())),
                "integrity": bool(self.var_rd_integrity.get()),
                "overload": bool(self.var_rd_overload.get()),
                "distinct_prefix": bool(self.var_rd_distinct.get()),
                "no_think": bool(self.var_rd_nothink.get()),
                "timeout": float(self.var_timeout.get() or 95),
            }
        except ValueError as e:
            return self._error(ValueError(f"Invalid number: {e}"))

        # Disable a Qwen3-style reasoning model's thinking for the whole test, so
        # the tool/agentic probes see the agentic mode (not chain-of-thought that
        # "overthinks" and answers in prose instead of calling the tool).
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}} if cfg["no_think"] else None
        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=cfg["timeout"], endpoint=self.var_endpoint.get(),
            extra_body=extra_body)
        self._remember_endpoint(target.host, target.port)
        # Stash for History persistence in _readiness_done (like the other tabs).
        self._rd_conn = (client.host, client.port, client.endpoint)
        self._rd_params = {"sweep_levels": ",".join(map(str, levels)),
                           "in": cfg["in_tokens"], "out": cfg["out_tokens"]}
        self._rd_rows = []

        for iid in self.ready_tree.get_children():
            self.ready_tree.delete(iid)
        self.ready_chart.clear()
        self.ready_log.clear()
        self.ready_log.write(f"▶ Provider fit · {client.base_url}", "head")
        thinkdesc = "thinking OFF (agentic mode)" if cfg["no_think"] else "thinking as-configured"
        self.ready_log.write(f"        sweep {levels} · in {cfg['in_tokens']} / out "
                             f"{cfg['out_tokens']} tok · TTFT SLA {cfg['ttft_sla_s']:g}s · "
                             f"{thinkdesc}", "dim")
        self.ready_readout.configure(text="Running compliance probes…")

        def on_progress(evt):
            self.post(lambda e=evt: self._readiness_progress(e))

        self.run_async(
            B.provider_readiness(client, self._resolved_model(),
                                 in_tokens=cfg["in_tokens"], out_tokens=cfg["out_tokens"],
                                 sweep_levels=tuple(levels),
                                 reqs_per_level=cfg["reqs_per_level"],
                                 ttft_sla_s=cfg["ttft_sla_s"], overload=cfg["overload"],
                                 distinct_prefix=cfg["distinct_prefix"],
                                 ctx_probe_tokens=cfg["ctx_probe_tokens"],
                                 integrity=cfg["integrity"],
                                 on_progress=on_progress),
            self._readiness_done, status="Provider-fit test running…")

    def _readiness_progress(self, evt: dict):
        ev = evt.get("event")
        if ev == "phase":
            self.ready_log.write(f"— {evt['label']} —", "head")
            self._set_status(evt["label"])
        elif ev == "note":
            self.ready_log.write(f"🧠 {evt['text']}", "head")
        elif ev == "check":
            self.ready_log.write(f"{'✓' if evt['ok'] else '✗'} {evt['name']}: {evt['detail']}",
                                 "ok" if evt["ok"] else "err")
        elif ev == "phase_done":
            self.ready_log.write(f"   {evt['name']}: {evt['passed']}/{evt['total']} checks passed",
                                 "ok" if evt["passed"] == evt["total"] else "err")
        elif ev == "sweep":
            r = evt["row"]
            tag = "ovl" if r["overload"] else ("odd" if len(self._rd_rows) % 2 else "even")
            label = f"{r['conc']}{'⁺' if r['overload'] else ''}"
            self.ready_tree.insert(
                "", "end", tags=(tag,),
                values=(label, f"{r['out_tps']:.0f}", f"{r['ttft_p95']:.2f}s",
                        f"{r['tpot_ms']:.0f}" if r['tpot_ms'] > 0 else "–",
                        f"{r['success']}/{r['requests']}",
                        f"{r['rejected']}", f"{r['hard_err']}"))
            self.ready_tree.yview_moveto(1.0)
            self._rd_rows.append(r)
            self.ready_chart.plot(
                [(f"c{x['conc']}", x["out_tps"]) for x in self._rd_rows],
                title="output tok/s vs concurrency", unit="tok/s")
            peak = max(x["out_tps"] for x in self._rd_rows)
            self.ready_readout.configure(
                text=f"Sweeping… c={label}: {r['out_tps']:.0f} out tok/s · "
                     f"TTFT p95 {r['ttft_p95']:.2f}s · peak {peak:.0f} tok/s")

    def _readiness_done(self, report: dict):
        a = report["analysis"]
        v = report["verdicts"]
        orv, hfv = v["openrouter"]["verdict"], v["huggingface"]["verdict"]

        def tone(text):
            return (self.pal["live_ok"] if text.startswith("✅")
                    else "#d0902a" if text.startswith("⚠") else self.pal["live_err"])
        self.ready_readout.configure(
            text=f"OpenRouter:  {orv}\nHuggingFace: {hfv}", text_color=tone(orv))

        self.ready_log.write("✓ Provider-fit test complete", "ok")
        if report.get("reasoning_model"):
            self.ready_log.write("🧠 Reasoning model — probes ran with expanded budget "
                                 "and <think> stripped.", "head")
        self.ready_log.write(f"Bottleneck: {a['text']}",
                             "ok" if a["type"] in ("healthy", "insufficient") else "err")

        integ = report.get("integrity")
        if integ:
            tok = integ.get("token_honesty", {})
            tok_flag = ("⚠ inflation" if tok.get("ok") is False
                        else "n/a" if tok.get("ok") is None else "honest")
            q = integ.get("quality", {})
            cx = integ.get("context_honesty", {})
            self.ready_log.write(
                f"Integrity: tokens {tok_flag} · quality {q.get('score', 0) * 100:.0f}% · "
                f"context {cx.get('passed', 0)}/{cx.get('total', 0)}",
                "ok" if (tok.get("ok") is not False and q.get("ok", True) and cx.get("ok", True))
                else "err")

        for prov, key in (("OpenRouter", "openrouter"), ("HuggingFace", "huggingface")):
            info = v[key]
            rows = [(g, "✓" if ok else "✗ missing") for g, ok in info["gates"].items()]
            rows.append(("peak output tok/s", f"{a['peak_out_tps']:.0f} @ c={a['peak_conc']}"))
            rows.append(("throughput knee", f"c={a['knee_conc']} "
                         f"(TTFT p95 {a['ttft_p95_knee']:.2f}s)"))
            rows.append(("TTFT p95 / p99 @ knee",
                         f"{a['ttft_p95_knee']:.2f}s / {a.get('ttft_p99_knee', 0):.2f}s"))
            rows.append(("latency p99 @ knee", f"{a.get('lat_p99_knee', 0):.2f}s"))
            rows.append(("admission control", a["admission"]))
            self.ready_log.result(prov, info["verdict"], rows,
                                  failed=not info["verdict"].startswith("✅"))

        # Persist to History + comparison (value = peak output tok/s), like the
        # Benchmark and Model-fit tabs.
        conn = getattr(self, "_rd_conn", None)
        if conn:
            host, port, endpoint = conn
            params = getattr(self, "_rd_params", {})
            summary = (f"OR {orv[:2]} · HF {hfv[:2]} · {a['type']} · "
                       f"peak {a['peak_out_tps']:.0f} tok/s @ c={a['peak_conc']}")
            self._record_and_compare(
                host, port, report.get("model", ""), endpoint, "readiness",
                params, summary, self._readiness_history_rows(report),
                value=a["peak_out_tps"], value_label="peak out tok/s")
        self._set_status("Provider-fit test complete.")

    @staticmethod
    def _readiness_history_rows(report: dict) -> list:
        """Flat (label, value) rows persisted to History for run-over-run compare."""
        a = report["analysis"]
        v = report["verdicts"]
        rows = [
            ("OpenRouter", v["openrouter"]["verdict"]),
            ("HuggingFace", v["huggingface"]["verdict"]),
            ("reasoning model", "yes" if report.get("reasoning_model") else "no"),
            ("bottleneck", a["type"]),
            ("admission control", a["admission"]),
            ("peak out tok/s", f"{a['peak_out_tps']:.0f} @ c={a['peak_conc']}"),
            ("throughput knee", f"c={a['knee_conc']}"),
            ("scale (c1→top)", f"×{a['scale']:.1f}"),
            ("TTFT p95 @ knee", f"{a['ttft_p95_knee']:.2f}s"),
            ("TTFT p99 @ knee", f"{a.get('ttft_p99_knee', 0):.2f}s"),
            ("latency p99 @ knee", f"{a.get('lat_p99_knee', 0):.2f}s"),
        ]
        integ = report.get("integrity", {})
        if integ:
            tok = integ.get("token_honesty", {})
            rows.append(("token honesty", tok.get("detail", "—")))
            rows.append(("context honesty", integ.get("context_honesty", {}).get("detail", "—")))
            rows.append(("model quality", integ.get("quality", {}).get("detail", "—")))
            rows.append(("cancellation", integ.get("cancellation", {}).get("detail", "—")))
            rows.append(("logprob", integ.get("logprob", {}).get("detail", "—")))
        for c in report["compliance"]:
            rows.append((c["name"], "✓" if c["ok"] else "✗"))
        return rows

    def copy_readiness_table(self):
        """Copy the concurrency-sweep table (tab-separated), like Benchmark/Optimum finder."""
        if not self.ready_tree.get_children():
            messagebox.showinfo(APP_TITLE, "No provider-fit results to copy yet — run a test first.")
            return
        rows = self._tree_to_rows(self.ready_tree)
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join("\t".join(row) for row in rows))
        self._set_status(f"Copied sweep table ({len(rows) - 1} rows) to the clipboard.")
        self.ready_log.write(f"📋 Copied sweep table ({len(rows) - 1} rows) to clipboard", "ok")

    def copy_readiness_report(self):
        """Copy the full test transcript (compliance/integrity/verdicts) as plain text."""
        text = self.ready_log.get_text().strip()
        if not text:
            messagebox.showinfo(APP_TITLE, "No provider-fit report to copy yet — run a test first.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("Copied the provider-fit report to the clipboard.")
        self.ready_log.write("📋 Copied full report to clipboard", "ok")

    # ------------------------------------------------------- Capabilities tab
    _CAP_SYM = {"yes": "✓ supported", "no": "✗ no", "maybe": "~ present",
                "error": "⚠ error", "na": "— n/a"}

    def _build_capabilities_tab(self):
        sec, top = self._section(self.tab_caps, "Capabilities — what this endpoint/model offers")
        sec.pack(fill="x", padx=12, pady=(10, 6))
        intro = ctk.CTkLabel(
            top, anchor="w", justify="left", text_color=self.pal["sub"], wraplength=760,
            text=("Discovers which API routes the server serves (embeddings, rerank, tokenize, "
                  "audio, images, …) and which chat features the model supports (streaming, "
                  "tool-calling, JSON mode, vision, logprobs, seed, reasoning). Each row is one "
                  "small probe against the current Host / Model."))
        intro.pack(fill="x", padx=12, pady=(4, 8))

        def _rewrap(e, lbl=intro):
            want = max(320, e.width - 28)
            if abs(lbl.cget("wraplength") - want) > 12:
                lbl.configure(wraplength=want)
        top.bind("<Configure>", _rewrap)

        runbar = ctk.CTkFrame(self.tab_caps, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_caps = ctk.CTkButton(runbar, text=self.L("Run capability scan"),
                                      command=self.on_run_capabilities)
        self.btn_caps.pack(side="left")
        btn_caps_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                        fg_color="#b04a4a", hover_color="#963c3c",
                                        command=self.cancel_current)
        btn_caps_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_caps_cancel)
        ctk.CTkButton(runbar, text=self.L("Copy results"), width=110,
                      command=self.copy_capabilities_results).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(runbar, text="A couple dozen quick probes — no load generated.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.caps_readout = ctk.CTkLabel(
            self.tab_caps, text=self.L("Press ‘Run capability scan’ to inventory this endpoint."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.caps_readout.pack(fill="x", padx=16, pady=(2, 4))

        sec3, body = self._section(self.tab_caps, "Capabilities")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        cols = ("feature", "status", "detail")
        wrap, self.caps_tree = self._tree_with_scrollbars(body, cols, height=16)
        for c, w, txt, stretch in (("feature", 320, "Capability", False),
                                   ("status", 130, "Support", False),
                                   ("detail", 520, "Details", True)):
            self.caps_tree.heading(c, text=txt)
            self.caps_tree.column(c, width=w, anchor="w", stretch=stretch)
        self.caps_tree.tag_configure("cat", background=self.pal["head_bg"],
                                     foreground=self.pal["head_fg"])
        self.caps_tree.tag_configure("yes", foreground=self.pal["live_ok"])
        self.caps_tree.tag_configure("no", foreground=self.pal["sub"])
        self.caps_tree.tag_configure("maybe", foreground=self.pal["warn"])
        self.caps_tree.tag_configure("error", foreground=self.pal["live_err"])
        self.caps_tree.tag_configure("na", foreground=self.pal["sub"])
        wrap.pack(fill="both", expand=True)

    def on_run_capabilities(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
        except ValueError as e:
            return self._error(ValueError(f"Invalid host/port: {e}"))
        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=float(self.var_timeout.get() or 95), endpoint=self.var_endpoint.get())
        self._remember_endpoint(target.host, target.port)
        self.caps_tree.delete(*self.caps_tree.get_children())
        self._caps_report = None
        self.caps_readout.configure(text=f"Scanning {client.base_url} …",
                                    text_color=self.caps_readout.cget("text_color"))

        def on_progress(evt):
            self.post(lambda e=evt: self._capabilities_progress(e))

        self.run_async(
            B.capabilities_probe(client, self._resolved_model(), on_progress=on_progress),
            self._capabilities_done, status="Capability scan running…")

    def _capabilities_progress(self, evt: dict):
        kind = evt.get("event")
        if kind == "status":
            self._set_status(evt.get("text", "Capability scan running…"))
        elif kind == "group":
            self.caps_tree.insert("", "end", values=(f"▸ {evt['group']}", "", ""), tags=("cat",))
        elif kind == "item":
            it = evt["item"]
            self.caps_tree.insert(
                "", "end", tags=(it["status"],),
                values=(f"    {it['name']}", self._CAP_SYM.get(it["status"], it["status"]),
                        it.get("detail", "")))
            self.caps_tree.see(self.caps_tree.get_children()[-1])

    def _capabilities_done(self, report: dict):
        self._caps_report = report
        n, tot = report.get("supported", 0), report.get("total", 0)
        GREEN = ("#1c8a44", "#57c07a")
        self.caps_readout.configure(
            text=f"{n} / {tot} capabilities supported   ·   model {report.get('model', '?')}",
            text_color=GREEN)
        self._set_status(f"Capability scan complete — {n}/{tot} supported.")

    def copy_capabilities_results(self):
        rep = getattr(self, "_caps_report", None)
        if not rep:
            messagebox.showinfo(APP_TITLE, "No capability scan to copy yet — run one first.")
            return
        lines = [f"Capabilities — {rep.get('model', '?')} "
                 f"({rep.get('supported', 0)}/{rep.get('total', 0)} supported)", ""]
        for g in rep.get("groups", []):
            lines.append(f"== {g['group']} ==")
            for it in g["items"]:
                lines.append(f"  {self._CAP_SYM.get(it['status'], it['status'])}\t"
                             f"{it['name']}\t{it.get('detail', '')}")
            lines.append("")
        text = "\n".join(lines).rstrip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("Copied capabilities to the clipboard.")

    # -------------------------------------------------------- Embed speed tab
    def _build_embed_tab(self):
        self.var_emb_model = tk.StringVar(value="")
        self.var_emb_batch = tk.StringVar(value="32")
        self.var_emb_conc = tk.StringVar(value="8")
        self.var_emb_intok = tk.StringVar(value="64")
        self.var_emb_dur = tk.StringVar(value="15")

        sec, top = self._section(self.tab_embed, "Embedding speed (throughput & latency)")
        sec.pack(fill="x", padx=12, pady=(10, 6))
        for col, ms in ((0, 150), (1, 150), (2, 30), (3, 150), (4, 150)):
            top.grid_columnconfigure(col, minsize=ms)
        top.grid_columnconfigure(5, weight=1)

        self._lbl(top, "Embedding model", INFO["emb_model"]).grid(
            row=0, column=0, sticky="e", padx=(12, 6), pady=6)
        ctk.CTkEntry(top, textvariable=self.var_emb_model, width=320,
                     placeholder_text="(uses the model selected at the top)").grid(
            row=0, column=1, columnspan=4, sticky="w", pady=6)

        def field(r, pair, label, var, info, w=130):
            col = 0 if pair == 0 else 3
            self._lbl(top, label, info).grid(row=r, column=col, sticky="e", padx=(12, 6), pady=6)
            ctk.CTkEntry(top, textvariable=var, width=w).grid(row=r, column=col + 1, sticky="w", pady=6)

        field(1, 0, "Batch size (texts/req)", self.var_emb_batch, INFO["emb_batch"])
        field(1, 1, "Concurrency", self.var_emb_conc, INFO["emb_conc"])
        field(2, 0, "Input tokens / text", self.var_emb_intok, INFO["emb_intok"])
        field(2, 1, "Duration (s)", self.var_emb_dur, INFO["emb_dur"])

        runbar = ctk.CTkFrame(self.tab_embed, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_embed = ctk.CTkButton(runbar, text=self.L("Run embed speed test"),
                                       command=self.on_run_embed)
        self.btn_embed.pack(side="left")
        btn_embed_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                         fg_color="#b04a4a", hover_color="#963c3c",
                                         command=self.cancel_current)
        btn_embed_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_embed_cancel)
        ctk.CTkLabel(runbar, text="Holds a batched embedding load and reports embeddings/s, "
                                  "tokens/s and latency — raise batch size for peak throughput.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.embed_readout = ctk.CTkLabel(
            self.tab_embed, text=self.L("Pick an embedding model and press ‘Run embed speed test’."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.embed_readout.pack(fill="x", padx=16, pady=(2, 4))

        sec3, body = self._section(self.tab_embed, "Live")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        split = ttk.PanedWindow(body, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ctk.CTkFrame(split, fg_color="transparent")
        right = ctk.CTkFrame(split)
        split.add(left, weight=3)
        split.add(right, weight=2)
        self.embed_chart = ChartCanvas(left, height=220)
        self.embed_chart.pack(fill="both", expand=True)
        self.embed_log = LiveLog(right, self.pal, fg_color="transparent")
        self.embed_log.pack(fill="both", expand=True)

    def on_run_embed(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
            cfg = {
                "batch_size": max(1, int(self.var_emb_batch.get())),
                "concurrency": max(1, int(self.var_emb_conc.get())),
                "input_tokens": max(1, int(self.var_emb_intok.get())),
                "duration_s": max(1.0, float(self.var_emb_dur.get())),
                "timeout": float(self.var_timeout.get() or 95),
            }
        except ValueError as e:
            return self._error(ValueError(f"Invalid number: {e}"))
        model = self.var_emb_model.get().strip() or self._resolved_model()
        if not model:
            return self._error(ValueError("No embedding model — enter one or select a model at the top."))

        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=cfg["timeout"], endpoint=self.var_endpoint.get())
        self._remember_endpoint(target.host, target.port)
        self.embed_chart.clear()
        self.embed_log.clear()
        self._embed_default_color = self.embed_readout.cget("text_color")
        self.embed_log.write(f"▶ Embed speed · {client.base_url}", "head")
        self.embed_log.write(
            f"        model {model} · batch {cfg['batch_size']} · c={cfg['concurrency']} · "
            f"{cfg['input_tokens']} tok/text · {cfg['duration_s']:g}s", "dim")
        self.embed_readout.configure(text=f"Preflight embed for {model} …",
                                     text_color=self._embed_default_color)

        def on_progress(snap):
            self.post(lambda s=snap: self._embed_progress(s))

        self.run_async(
            B.embed_speed_test(client, model, batch_size=cfg["batch_size"],
                               concurrency=cfg["concurrency"], input_tokens=cfg["input_tokens"],
                               duration_s=cfg["duration_s"], on_progress=on_progress),
            self._embed_done, status="Embed speed test running…")

    def _embed_readout_text(self, s: dict) -> str:
        est = "  ⚠ est-tokens" if s.get("est_frac", 0) >= 0.5 else ""
        return (f"⏱ {self._fmt_hms(s['elapsed'])} / {self._fmt_hms(s['duration'])}   "
                f"(dim {s['dim']} · {s['success']} req ok · {s['errors']} err)\n"
                f"EMBEDDINGS  {s['emb_per_s']:>10,.0f} /s   ·   INPUT {s['tok_per_s']:>12,.0f} tok/s\n"
                f"batch {s['batch_size']} · {s['req_per_s']:.1f} req/s · latency p50 {s['lat_p50']*1000:.0f}ms "
                f"/ p95 {s['lat_p95']*1000:.0f}ms · {s['ms_per_emb']:.2f} ms/emb{est}")

    def _embed_progress(self, s: dict):
        self.embed_readout.configure(text=self._embed_readout_text(s))
        self._set_status(f"Embed: {self._fmt_hms(s['remaining'])} left · {s['emb_per_s']:.0f} emb/s")
        if s.get("series"):
            self.embed_chart.plot(s["series"], title="embeddings/s over time", unit="emb/s")

    def _embed_done(self, s: dict):
        self._embed_progress(s)
        GREEN, RED = ("#1c8a44", "#57c07a"), ("#b23b3b", "#e26d6d")
        ok = s["success"] > 0
        self.embed_log.write("✓ Embed speed complete" if ok else "✗ Embed speed failed",
                             "ok" if ok else "err")
        self.embed_readout.configure(text_color=GREEN if ok else RED)
        self.embed_log.result(
            "Embedding throughput",
            f"{s['emb_per_s']:,.0f} emb/s · {s['tok_per_s']:,.0f} tok/s (dim {s['dim']})",
            [("embeddings / s", f"{s['emb_per_s']:,.0f}"),
             ("input tokens / s", f"{s['tok_per_s']:,.0f}"),
             ("requests / s", f"{s['req_per_s']:.2f}"),
             ("vector dimension", f"{s['dim']}"),
             ("batch size", f"{s['batch_size']}"),
             ("concurrency", f"{s['concurrency']}"),
             ("latency p50 / p95 (ms)",
              f"{s['lat_p50']*1000:.0f} / {s['lat_p95']*1000:.0f}"),
             ("ms per embedding", f"{s['ms_per_emb']:.3f}"),
             ("total embedded", f"{s['embeddings']:,}"),
             ("input tokens", f"{s['tokens']:,}"),
             ("requests", f"{s['success']} ok / {s['errors']} failed"),
             ("token counts", "estimated" if s.get("est_frac", 0) >= 0.5 else "server-reported")],
            failed=not ok)
        for e in s.get("error_samples", []):
            self.embed_log.write(f"   error: {e[:80]}", "err")
        self._set_status("Embed speed test complete.")

    # ------------------------------------------------------ Embed quality tab
    def _build_embed_quality_tab(self):
        self.var_embq_model = tk.StringVar(value="")
        sec, top = self._section(self.tab_embq, "Embedding quality (does it actually work?)")
        sec.pack(fill="x", padx=12, pady=(10, 6))
        intro = ctk.CTkLabel(
            top, anchor="w", justify="left", text_color=self.pal["sub"], wraplength=760,
            text=("Checks embedding QUALITY, not speed: retrieval ranking, paraphrase vs unrelated "
                  "similarity, Estonian↔English cross-lingual alignment, vector properties "
                  "(L2-normalised, deterministic, dimension), input/batch limits, and — if the "
                  "server serves /v1/rerank — reranker relevance."))
        intro.pack(fill="x", padx=12, pady=(4, 6))

        def _rewrap(e, lbl=intro):
            want = max(320, e.width - 28)
            if abs(lbl.cget("wraplength") - want) > 12:
                lbl.configure(wraplength=want)
        top.bind("<Configure>", _rewrap)

        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 4))
        self._lbl(row, "Embedding model", INFO["emb_model"]).pack(side="left", padx=(0, 6))
        ctk.CTkEntry(row, textvariable=self.var_embq_model, width=320,
                     placeholder_text="(uses the model selected at the top)").pack(side="left")

        runbar = ctk.CTkFrame(self.tab_embq, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_embq = ctk.CTkButton(runbar, text=self.L("Run embed quality test"),
                                      command=self.on_run_embed_quality)
        self.btn_embq.pack(side="left")
        btn_embq_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                        fg_color="#b04a4a", hover_color="#963c3c",
                                        command=self.cancel_current)
        btn_embq_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_embq_cancel)
        ctk.CTkButton(runbar, text=self.L("Copy results"), width=110,
                      command=self.copy_embq_results).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(runbar, text="~20 quick probes — no load generated.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.embq_readout = ctk.CTkLabel(
            self.tab_embq, text=self.L("Pick an embedding model and press ‘Run embed quality test’."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.embq_readout.pack(fill="x", padx=16, pady=(2, 4))
        self._embq_default_color = self.embq_readout.cget("text_color")

        sec3, body = self._section(self.tab_embq, "Checks")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        cols = ("feature", "status", "detail")
        wrap, self.embq_tree = self._tree_with_scrollbars(body, cols, height=15)
        for c, w, txt, stretch in (("feature", 300, "Check", False),
                                   ("status", 120, "Result", False),
                                   ("detail", 540, "Details", True)):
            self.embq_tree.heading(c, text=txt)
            self.embq_tree.column(c, width=w, anchor="w", stretch=stretch)
        self.embq_tree.tag_configure("cat", background=self.pal["head_bg"],
                                     foreground=self.pal["head_fg"])
        self.embq_tree.tag_configure("yes", foreground=self.pal["live_ok"])
        self.embq_tree.tag_configure("no", foreground=self.pal["live_err"])
        self.embq_tree.tag_configure("maybe", foreground=self.pal["warn"])
        self.embq_tree.tag_configure("error", foreground=self.pal["live_err"])
        self.embq_tree.tag_configure("na", foreground=self.pal["sub"])
        wrap.pack(fill="both", expand=True)

    _EMBQ_SYM = {"yes": "✓ pass", "no": "✗ fail", "maybe": "~ weak",
                 "error": "⚠ error", "na": "— n/a"}

    def on_run_embed_quality(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
        except ValueError as e:
            return self._error(ValueError(f"Invalid host/port: {e}"))
        model = self.var_embq_model.get().strip() or self._resolved_model()
        if not model:
            return self._error(ValueError("No embedding model — enter one or select a model at the top."))
        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=float(self.var_timeout.get() or 95), endpoint=self.var_endpoint.get())
        self._remember_endpoint(target.host, target.port)
        self.embq_tree.delete(*self.embq_tree.get_children())
        self._embq_report = None
        self.embq_readout.configure(text=f"Preflight embed for {model} …",
                                    text_color=self._embq_default_color)

        def on_progress(evt):
            self.post(lambda e=evt: self._embq_progress(e))

        self.run_async(
            B.embed_quality_test(client, model, on_progress=on_progress),
            self._embq_done, status="Embed quality test running…")

    def _embq_progress(self, evt: dict):
        kind = evt.get("event")
        if kind == "status":
            self._set_status(evt.get("text", "Embed quality test running…"))
        elif kind == "group":
            self.embq_tree.insert("", "end", values=(f"▸ {evt['group']}", "", ""), tags=("cat",))
        elif kind == "item":
            it = evt["item"]
            self.embq_tree.insert(
                "", "end", tags=(it["status"],),
                values=(f"    {it['name']}", self._EMBQ_SYM.get(it["status"], it["status"]),
                        it.get("detail", "")))
            self.embq_tree.see(self.embq_tree.get_children()[-1])

    def _embq_done(self, report: dict):
        self._embq_report = report
        n, tot = report.get("supported", 0), report.get("total", 0)
        GREEN, RED = ("#1c8a44", "#57c07a"), ("#b23b3b", "#e26d6d")
        self.embq_readout.configure(
            text=f"{n} / {tot} checks passed   ·   model {report.get('model', '?')} "
                 f"(dim {report.get('dim', '?')})",
            text_color=GREEN if n == tot else (RED if n <= tot // 2 else self._embq_default_color))
        self._set_status(f"Embed quality test complete — {n}/{tot} passed.")

    def copy_embq_results(self):
        rep = getattr(self, "_embq_report", None)
        if not rep:
            messagebox.showinfo(APP_TITLE, "No embed quality report to copy yet — run one first.")
            return
        lines = [f"Embedding quality — {rep.get('model', '?')} (dim {rep.get('dim', '?')}) "
                 f"— {rep.get('supported', 0)}/{rep.get('total', 0)} passed", ""]
        for g in rep.get("groups", []):
            lines.append(f"== {g['group']} ==")
            for it in g["items"]:
                lines.append(f"  {self._EMBQ_SYM.get(it['status'], it['status'])}\t"
                             f"{it['name']}\t{it.get('detail', '')}")
            lines.append("")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines).rstrip())
        self._set_status("Copied embed quality results to the clipboard.")

    # ------------------------------------------------------------- Vision tab
    _VIS_SYM = {"yes": "✓ pass", "no": "✗ fail", "maybe": "~ partial",
                "error": "⚠ error", "na": "— n/a"}

    def _build_vision_tab(self):
        self.var_vis_model = tk.StringVar(value="")
        sec, top = self._section(self.tab_vision, "Vision (VL) — does the model understand images?")
        sec.pack(fill="x", padx=12, pady=(10, 6))
        intro = ctk.CTkLabel(
            top, anchor="w", justify="left", text_color=self.pal["sub"], wraplength=760,
            text=("Sends generated images with known content — solid colours, blocky text and "
                  "numbers, a row of squares, and two images at once — and checks the model's "
                  "answers against ground truth. Tests real understanding (colour, OCR, counting, "
                  "multi-image), not just whether the server accepts an image."))
        intro.pack(fill="x", padx=12, pady=(4, 6))

        def _rewrap(e, lbl=intro):
            want = max(320, e.width - 28)
            if abs(lbl.cget("wraplength") - want) > 12:
                lbl.configure(wraplength=want)
        top.bind("<Configure>", _rewrap)

        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 4))
        self._lbl(row, "Vision model", INFO["vis_model"]).pack(side="left", padx=(0, 6))
        ctk.CTkEntry(row, textvariable=self.var_vis_model, width=320,
                     placeholder_text="(uses the model selected at the top)").pack(side="left")

        runbar = ctk.CTkFrame(self.tab_vision, fg_color="transparent")
        runbar.pack(fill="x", padx=12, pady=4)
        self.btn_vision = ctk.CTkButton(runbar, text=self.L("Run vision test"),
                                        command=self.on_run_vision)
        self.btn_vision.pack(side="left")
        btn_vision_cancel = ctk.CTkButton(runbar, text=self.L("Stop"), width=80, state="disabled",
                                          fg_color="#b04a4a", hover_color="#963c3c",
                                          command=self.cancel_current)
        btn_vision_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_vision_cancel)
        ctk.CTkButton(runbar, text=self.L("Copy results"), width=110,
                      command=self.copy_vision_results).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(runbar, text="~8 image probes — needs a vision-language (VL) model.",
                     text_color=self.pal["sub"]).pack(side="left", padx=10)

        self.vision_readout = ctk.CTkLabel(
            self.tab_vision, text=self.L("Pick a VL model and press ‘Run vision test’."),
            anchor="w", justify="left", font=ctk.CTkFont(size=15, weight="bold"))
        self.vision_readout.pack(fill="x", padx=16, pady=(2, 4))
        self._vision_default_color = self.vision_readout.cget("text_color")

        sec3, body = self._section(self.tab_vision, "Checks")
        sec3.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        cols = ("feature", "status", "detail")
        wrap, self.vision_tree = self._tree_with_scrollbars(body, cols, height=13)
        for c, w, txt, stretch in (("feature", 260, "Check", False),
                                   ("status", 120, "Result", False),
                                   ("detail", 580, "Details", True)):
            self.vision_tree.heading(c, text=txt)
            self.vision_tree.column(c, width=w, anchor="w", stretch=stretch)
        self.vision_tree.tag_configure("cat", background=self.pal["head_bg"],
                                       foreground=self.pal["head_fg"])
        self.vision_tree.tag_configure("yes", foreground=self.pal["live_ok"])
        self.vision_tree.tag_configure("no", foreground=self.pal["live_err"])
        self.vision_tree.tag_configure("maybe", foreground=self.pal["warn"])
        self.vision_tree.tag_configure("error", foreground=self.pal["live_err"])
        self.vision_tree.tag_configure("na", foreground=self.pal["sub"])
        wrap.pack(fill="both", expand=True)

    def on_run_vision(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
        except ValueError as e:
            return self._error(ValueError(f"Invalid host/port: {e}"))
        model = self.var_vis_model.get().strip() or self._resolved_model()
        if not model:
            return self._error(ValueError("No model — enter a VL model or select one at the top."))
        client = LLMClient.from_target(
            target, api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=float(self.var_timeout.get() or 95), endpoint=self.var_endpoint.get())
        self._remember_endpoint(target.host, target.port)
        self.vision_tree.delete(*self.vision_tree.get_children())
        self._vision_report = None
        self.vision_readout.configure(text=f"Testing {model} …",
                                      text_color=self._vision_default_color)

        def on_progress(evt):
            self.post(lambda e=evt: self._vision_progress(e))

        self.run_async(
            B.vision_test(client, model, on_progress=on_progress),
            self._vision_done, status="Vision test running…")

    def _vision_progress(self, evt: dict):
        kind = evt.get("event")
        if kind == "status":
            self._set_status(evt.get("text", "Vision test running…"))
        elif kind == "group":
            self.vision_tree.insert("", "end", values=(f"▸ {evt['group']}", "", ""), tags=("cat",))
        elif kind == "item":
            it = evt["item"]
            self.vision_tree.insert(
                "", "end", tags=(it["status"],),
                values=(f"    {it['name']}", self._VIS_SYM.get(it["status"], it["status"]),
                        it.get("detail", "")))
            self.vision_tree.see(self.vision_tree.get_children()[-1])

    def _vision_done(self, report: dict):
        self._vision_report = report
        GREEN, RED = ("#1c8a44", "#57c07a"), ("#b23b3b", "#e26d6d")
        if not report.get("vision"):
            self.vision_readout.configure(
                text=f"NOT A VISION MODEL — {report.get('model', '?')} doesn't accept image input.",
                text_color=RED)
            self._set_status("Vision test complete — not a VL model.")
            return
        n, tot = report.get("supported", 0), report.get("total", 0)
        self.vision_readout.configure(
            text=f"{n} / {tot} vision checks passed   ·   model {report.get('model', '?')}",
            text_color=GREEN if n == tot else (RED if n <= tot // 2 else self._vision_default_color))
        self._set_status(f"Vision test complete — {n}/{tot} passed.")

    def copy_vision_results(self):
        rep = getattr(self, "_vision_report", None)
        if not rep:
            messagebox.showinfo(APP_TITLE, "No vision report to copy yet — run one first.")
            return
        lines = [f"Vision (VL) — {rep.get('model', '?')} — "
                 f"{rep.get('supported', 0)}/{rep.get('total', 0)} passed", ""]
        for g in rep.get("groups", []):
            lines.append(f"== {g['group']} ==")
            for it in g["items"]:
                lines.append(f"  {self._VIS_SYM.get(it['status'], it['status'])}\t"
                             f"{it['name']}\t{it.get('detail', '')}")
            lines.append("")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines).rstrip())
        self._set_status("Copied vision results to the clipboard.")

    def _build_scan_tab(self):
        sec, top = self._section(self.tab_scan, "Scan settings")
        sec.pack(fill="x", padx=12, pady=10)
        self.var_subnet = tk.StringVar(value=default_subnet())
        self.var_ports = tk.StringVar(value=",".join(str(p) for p in DEFAULT_PORTS))
        self.var_scan_timeout = tk.StringVar(value="1.0")
        self.var_scan_conc = tk.StringVar(value="256")

        self._lbl(top, "Subnet (CIDR)", INFO["scan_subnet"]).grid(row=0, column=0, sticky="e", padx=(0, 4), pady=5)
        ctk.CTkEntry(top, textvariable=self.var_subnet, width=190).grid(row=0, column=1, sticky="w", pady=5)
        self._lbl(top, "Timeout (s)", INFO["scan_timeout"]).grid(row=0, column=2, sticky="e", padx=(12, 4))
        ctk.CTkEntry(top, textvariable=self.var_scan_timeout, width=80).grid(row=0, column=3, sticky="w")
        self._lbl(top, "Concurrency", INFO["scan_conc"]).grid(row=0, column=4, sticky="e", padx=(12, 4))
        ctk.CTkEntry(top, textvariable=self.var_scan_conc, width=80).grid(row=0, column=5, sticky="w")
        self._lbl(top, "Ports", INFO["scan_ports"]).grid(row=1, column=0, sticky="e", padx=(0, 4), pady=5)
        ctk.CTkEntry(top, textvariable=self.var_ports, width=620).grid(
            row=1, column=1, columnspan=5, sticky="w", pady=5)

        btns = ctk.CTkFrame(self.tab_scan, fg_color="transparent")
        btns.pack(fill="x", padx=12)
        self.btn_scan = ctk.CTkButton(btns, text=self.L("Scan network"), command=self.on_scan)
        self.btn_scan.pack(side="left")
        ctk.CTkButton(btns, text=self.L("Use selected server"), command=self.on_use_server).pack(side="left", padx=8)
        self.scan_progress = ctk.CTkProgressBar(btns, width=260)
        self.scan_progress.set(0)
        self.scan_progress.pack(side="right", padx=6)

        sec2, body = self._section(self.tab_scan, "Discovered servers")
        sec2.pack(fill="both", expand=True, padx=12, pady=10)
        cols = ("host", "port", "type", "api", "models")
        wrap, self.scan_tree = self._tree_with_scrollbars(body, cols, height=14, reorder=True)
        wrap.pack(fill="both", expand=True)
        for c, w in (("host", 130), ("port", 70), ("type", 130), ("api", 90), ("models", 380)):
            self.scan_tree.heading(c, text=c.upper() if c == "api" else c.capitalize())
            self.scan_tree.column(c, width=w, minwidth=50, anchor="w",
                                  stretch=(c == "models"))

        ctk.CTkLabel(self.tab_scan, text="Note: only scan networks you own or are authorised to test.",
                     text_color=self.pal["warn"]).pack(anchor="w", padx=14, pady=(0, 8))

    def _build_history_tab(self):
        bar = ctk.CTkFrame(self.tab_history, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=8)
        ctk.CTkButton(bar, text=self.L("Refresh"), width=80, command=self.refresh_history).pack(side="left")
        ctk.CTkButton(bar, text=self.L("Export CSV…"), width=100, command=self.export_history).pack(side="left", padx=6)
        ctk.CTkButton(bar, text=self.L("Compare selected"), width=130,
                      command=self._compare_selected).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text=self.L("Export report"), width=120,
                      command=self._export_report).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text=self.L("Clear all"), width=80, fg_color="#b04a4a", hover_color="#963c3c",
                      command=self.clear_history).pack(side="left")
        self._lbl(bar, "Filter:", INFO["hist_filter"]).pack(side="left", padx=(16, 4))
        self.var_hist_filter = tk.StringVar()
        ctk.CTkEntry(bar, textvariable=self.var_hist_filter, width=220).pack(side="left")
        ctk.CTkButton(bar, text="✕", width=28,
                      command=lambda: self.var_hist_filter.set("")).pack(side="left", padx=4)
        self.var_hist_filter.trace_add("write", lambda *a: self._render_history())
        self.hist_count = ctk.CTkLabel(bar, text="")
        self.hist_count.pack(side="right", padx=8)

        body = ctk.CTkFrame(self.tab_history, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self._hist_cols = ("when", "server", "model", "test", "summary")
        widths = {"when": 150, "server": 150, "model": 170, "test": 110, "summary": 360}
        wrap, self.history_tree = self._tree_with_scrollbars(
            body, self._hist_cols, height=14, reorder=True)
        wrap.pack(fill="both", expand=True)
        for c in self._hist_cols:
            self.history_tree.heading(c, text=c.capitalize(),
                                      command=lambda cc=c: self._sort_history(cc))
            self.history_tree.column(c, width=widths[c], minwidth=60, anchor="w",
                                     stretch=(c == "summary"))
        self.history_tree.bind("<<TreeviewSelect>>", self._on_history_select)

        ctk.CTkLabel(self.tab_history,
                     text="Click a heading to sort · type to filter · select a row to chart it · "
                          "Cmd/Shift-click several rows, then ‘Compare selected’ or ‘Export report’.").pack(
            anchor="w", padx=14)
        self.history_chart = ChartCanvas(self.tab_history, height=170)
        self.history_chart.pack(fill="x", padx=12, pady=(2, 10))
        self.refresh_history()

    # ------------------------------------------------------------- saved hosts
    def _refresh_hosts(self):
        self._hosts = store.list_hosts()
        names = [h["name"] for h in self._hosts]
        self.host_select.configure(values=names or ["(no saved hosts)"])
        if not names:
            self.host_select.set("(no saved hosts)")

    def _refresh_host_suggestions(self):
        try:
            self.host_combo.configure(values=store.recent_hosts(50))
            self.port_combo.configure(values=store.recent_ports(limit=20))
        except Exception:
            pass

    def _on_host_pick(self, _e=None):
        host = self.var_host.get().strip()
        ports = store.recent_ports(host, limit=20)
        if ports:
            self.port_combo.configure(values=ports)
            self.var_port.set(ports[0])

    def _remember_endpoint(self, host, port):
        try:
            store.record_endpoint(host, port)
        except Exception:
            pass
        self._refresh_host_suggestions()

    def _current_profile(self, name: str) -> dict:
        def _i(var, d):
            try:
                return int(var.get())
            except Exception:
                return d

        def _f(var, d):
            try:
                return float(var.get())
            except Exception:
                return d
        return {
            "name": name,
            "host": self.var_host.get().strip(),
            "port": _i(self.var_port, 8000),
            "api_key": self.var_apikey.get().strip() or "EMPTY",
            "endpoint": self.var_endpoint.get(),
            "model": self.var_model.get().strip(),
            "tokens": _i(self.var_tokens, 256),
            "ctx": _i(self.var_ctx, 2048),
            "runs": _i(self.var_runs, 3),
            "concurrency": _i(self.var_conc, 8),
            "requests": _i(self.var_reqs, 32),
            "timeout": _f(self.var_timeout, 120.0),
        }

    def on_save_host(self):
        host = self.var_host.get().strip()
        port = self.var_port.get().strip()
        model = self.var_model.get().strip()
        default = " · ".join(p for p in (host, model, port) if p)
        name = simpledialog.askstring("Save host", "Profile name:",
                                      initialvalue=default, parent=self.root)
        if not name:
            return
        store.save_host(self._current_profile(name.strip()))
        self._remember_endpoint(host, port)
        self._refresh_hosts()
        self.host_select.set(name.strip())
        self._set_status(f"Saved host profile '{name.strip()}'.")

    def on_load_host(self):
        name = self.host_select.get()
        prof = next((h for h in self._hosts if h["name"] == name), None)
        if not prof:
            return
        self.var_host.set(str(prof.get("host") or ""))
        self.var_port.set(str(prof.get("port") or ""))
        self.var_apikey.set(str(prof.get("api_key") or "EMPTY"))
        self.var_endpoint.set(prof.get("endpoint") or "chat")
        self.var_model.set(prof.get("model") or "")
        for var, key in ((self.var_tokens, "tokens"), (self.var_ctx, "ctx"),
                         (self.var_runs, "runs"), (self.var_conc, "concurrency"),
                         (self.var_reqs, "requests"), (self.var_timeout, "timeout")):
            if prof.get(key) is not None:
                var.set(str(prof[key]))
        self._set_status(f"Loaded host profile '{name}'.")

    def on_delete_host(self):
        name = self.host_select.get()
        if not name or name.startswith("("):
            return
        if messagebox.askyesno(APP_TITLE, f"Delete host profile '{name}'?"):
            store.delete_host(name)
            self._refresh_hosts()
            self._set_status(f"Deleted host profile '{name}'.")

    # ------------------------------------------------------------- history tab
    def refresh_history(self):
        self._all_history = store.all_results(5000)
        self._render_history()

    def _sort_history(self, col: str):
        if getattr(self.history_tree, "_reordered", False):
            self.history_tree._reordered = False  # this click was a drag-reorder
            return
        cur_col, cur_rev = self._hist_sort
        rev = (not cur_rev) if col == cur_col else (col == "when")
        self._hist_sort = (col, rev)
        self._render_history()

    def _render_history(self):
        for i in self.history_tree.get_children():
            self.history_tree.delete(i)
        self._hist_by_iid = {}

        q = self.var_hist_filter.get().strip().lower()
        rows = self._all_history
        if q:
            def hay(r):
                return (f'{_fmt_ts(r["ts"])} {r["host"]}:{r["port"]} {r["model"]} '
                        f'{r["test_type"]} {r["summary"] or ""}').lower()
            rows = [r for r in rows if q in hay(r)]

        col, rev = self._hist_sort
        keyfn = {
            "when": lambda r: r["ts"],
            "server": lambda r: (r["host"] or "", r["port"] or 0),
            "model": lambda r: (r["model"] or "").lower(),
            "test": lambda r: r["test_type"] or "",
            "summary": lambda r: (r["summary"] or "").lower(),
        }[col]
        rows = sorted(rows, key=keyfn, reverse=rev)

        for n, r in enumerate(rows):
            iid = self.history_tree.insert("", "end", tags=("even" if n % 2 else "odd",),
                values=(
                    _fmt_ts(r["ts"]), f'{r["host"]}:{r["port"]}',
                    r["model"], r["test_type"], r["summary"] or ""))
            self._hist_by_iid[iid] = r

        arrow = " ▼" if rev else " ▲"
        for c in self._hist_cols:
            self.history_tree.heading(c, text=c.capitalize() + (arrow if c == col else ""))
        total = len(self._all_history)
        shown = len(rows)
        self.hist_count.configure(
            text=f"{shown} of {total} shown" if q else f"{total} result(s)")

    def _on_history_select(self, _e=None):
        sel = self.history_tree.selection()
        if not sel:
            return
        r = self._hist_by_iid.get(sel[0])
        if r:
            self._plot_config(self.history_chart, r["test_type"], r["config_hash"])

    def _plot_config(self, chart, test_type, cfg_hash):
        hist = list(reversed(store.history_for(cfg_hash, limit=200)))
        pts = [(time.strftime("%m-%d %H:%M", time.localtime(r["ts"])),
                r.get("value")) for r in hist]
        label = next((r["value_label"] for r in reversed(hist) if r.get("value_label")), "")
        title = f"{test_type} — {label or 'value'} over {len(hist)} run(s)"
        chart.plot(pts, title=title, unit=label)

    def clear_history(self):
        if messagebox.askyesno(APP_TITLE, "Delete ALL saved results? This cannot be undone."):
            store.clear_results()
            self.refresh_history()
            self._set_status("History cleared.")

    def export_history(self):
        path = filedialog.asksaveasfilename(
            title="Export results to CSV", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = store.all_results(100000)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "host", "port", "model", "endpoint",
                        "test_type", "config_hash", "summary"])
            for r in rows:
                w.writerow([_fmt_ts(r["ts"]), r["host"], r["port"], r["model"],
                            r["endpoint"], r["test_type"], r["config_hash"], r["summary"]])
        self._set_status(f"Exported {len(rows)} result(s) to {path}")

    # ------------------------------------------------- compare / report export
    def _selected_results(self) -> list:
        """Result dicts for the selected history rows, in view order."""
        return [self._hist_by_iid[i] for i in self.history_tree.selection()
                if i in self._hist_by_iid]

    @staticmethod
    def _result_metrics(r) -> list:
        """The (label, value) metric rows stored with a result."""
        try:
            data = json.loads(r["metrics_json"] or "{}")
            return [(str(k), str(v)) for k, v in (data.get("rows") or [])]
        except Exception:
            return []

    @staticmethod
    def _result_label(r) -> str:
        return f'{r["host"]}:{r["port"]} · {r["model"] or "?"} · {r["test_type"]}'

    def _merged_metric_order(self, rows) -> tuple:
        """Union of metric labels (first-seen order) + per-run {label: value}."""
        order, seen, per = [], set(), []
        for r in rows:
            m = self._result_metrics(r)
            per.append(dict(m))
            for k, _ in m:
                if k not in seen:
                    seen.add(k)
                    order.append(k)
        return order, per

    def _compare_selected(self):
        """Side-by-side comparison of the selected runs — across hosts/models/configs."""
        rows = self._selected_results()
        if len(rows) < 2:
            messagebox.showinfo(APP_TITLE, "Select 2+ history rows (Cmd/Shift-click) to compare.")
            return
        order, per = self._merged_metric_order(rows)
        win = ctk.CTkToplevel(self.root)
        win.title("Compare runs")
        win.geometry(self._sized_geometry(320 + 210 * len(rows), 600))
        win.transient(self.root)
        cols = ["metric"] + [f"r{i}" for i in range(len(rows))]
        wrap, tree = self._tree_with_scrollbars(win, cols, height=20, horizontal=True)
        tree.heading("metric", text="Metric")
        tree.column("metric", width=240, minwidth=140, anchor="w", stretch=False)
        for i, r in enumerate(rows):
            tree.heading(f"r{i}", text=self._result_label(r))
            tree.column(f"r{i}", width=200, minwidth=110, anchor="w", stretch=False)
        tree.insert("", "end", tags=("even",),
                    values=["summary"] + [(r["summary"] or "") for r in rows])
        tree.insert("", "end", tags=("odd",),
                    values=["when"] + [_fmt_ts(r["ts"]) for r in rows])
        for n, k in enumerate(order):
            tree.insert("", "end", tags=("even" if n % 2 else "odd",),
                        values=[k] + [per[i].get(k, "–") for i in range(len(rows))])
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkButton(win, text=self.L("Export report"), width=140,
                      command=lambda: self._export_report(rows)).pack(pady=(0, 10))

    def _markdown_report(self, rows) -> str:
        out = ["# LLM Scanner report", ""]
        if len(rows) == 1:
            r = rows[0]
            out += [f"- **Test:** {r['test_type']}",
                    f"- **Server:** {r['host']}:{r['port']}",
                    f"- **Model:** {r['model'] or '?'}",
                    f"- **Endpoint:** {r['endpoint']}",
                    f"- **When:** {_fmt_ts(r['ts'])}",
                    f"- **Summary:** {r['summary'] or ''}", "",
                    "| Metric | Value |", "|---|---|"]
            for k, v in self._result_metrics(r):
                out.append(f"| {k} | {v} |")
        else:
            order, per = self._merged_metric_order(rows)
            heads = [self._result_label(r) for r in rows]
            esc = lambda s: str(s).replace("|", "\\|")
            out += ["## Comparison", "",
                    "| Metric | " + " | ".join(esc(h) for h in heads) + " |",
                    "|" + "---|" * (len(rows) + 1),
                    "| summary | " + " | ".join(esc(r["summary"] or "") for r in rows) + " |",
                    "| when | " + " | ".join(_fmt_ts(r["ts"]) for r in rows) + " |"]
            for k in order:
                out.append(f"| {esc(k)} | "
                           + " | ".join(esc(per[i].get(k, "–")) for i in range(len(rows))) + " |")
        out += ["", f"_Generated by LLM Scanner · {_fmt_ts(time.time())}_"]
        return "\n".join(out)

    def _export_report(self, rows=None):
        rows = rows or self._selected_results()
        if not rows:
            messagebox.showinfo(APP_TITLE, "Select one or more history rows to export a report.")
            return
        path = filedialog.asksaveasfilename(
            title="Export report", defaultextension=".md",
            initialfile="llmscanner-report.md",
            filetypes=[("Markdown", "*.md"), ("HTML", "*.html")])
        if not path:
            return
        md = self._markdown_report(rows)
        if path.lower().endswith(".html"):
            body = md.replace("&", "&amp;").replace("<", "&lt;")
            content = ("<!doctype html><meta charset=\"utf-8\">"
                       "<title>LLM Scanner report</title>"
                       "<pre style=\"font:14px/1.5 -apple-system,monospace;padding:24px\">"
                       f"{body}</pre>")
        else:
            content = md
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self._set_status(f"Exported report ({len(rows)} run(s)) to {path}")

    # --------------------------------------------------------------- threading
    def post(self, fn):
        self.ui_queue.put(fn)

    def _drain_queue(self):
        try:
            while True:
                fn = self.ui_queue.get_nowait()
                try:
                    fn()
                except Exception as e:
                    self._set_status(f"UI error: {e}")
        except queue.Empty:
            pass
        self.root.after(60, self._drain_queue)

    def run_async(self, coro, on_done, *, status="Working…"):
        if self._busy:
            # Discard the un-awaited coroutine so it doesn't leak / warn.
            try:
                coro.close()
            except Exception:
                pass
            messagebox.showinfo(APP_TITLE, "A task is already running — please wait.")
            return
        self._set_busy(True, status)
        self._run_started = time.perf_counter()
        self._run_label = status

        def done_cb(fut):
            try:
                res = fut.result()
                self.post(lambda: self._finish(lambda: on_done(res), notify=True))
            except (concurrent.futures.CancelledError, asyncio.CancelledError):
                self.post(lambda: self._finish(self._on_cancelled))
            except Exception as e:
                self.post(lambda err=e: self._finish(lambda: self._error(err)))

        self._current_fut = self.runner.submit(coro, done_cb)

    def cancel_current(self):
        """Abort the running background task (optimum finder / benchmark / scan)."""
        fut = getattr(self, "_current_fut", None)
        if self._busy and fut is not None:
            self._set_status("Cancelling…")
            fut.cancel()

    def _on_cancelled(self):
        self._set_status("Cancelled.")
        for log in (getattr(self, "bench_log", None), getattr(self, "opt_log", None),
                    getattr(self, "soak_log", None), getattr(self, "capacity_log", None),
                    getattr(self, "embed_log", None), getattr(self, "fit_log", None),
                    getattr(self, "ready_log", None)):
            if log is not None:
                log.write("✗ Cancelled by user", "err")

    def _finish(self, fn, notify=False):
        self._set_busy(False)
        fn()
        # Desktop notification when a long-running test completes, so you can
        # walk away from it. Only for genuine completions (not cancel/error).
        started = getattr(self, "_run_started", None)
        self._run_started = None
        if notify and started is not None:
            elapsed = time.perf_counter() - started
            if elapsed >= 8.0 and store.get_setting("notify", "1") == "1":
                label = getattr(self, "_run_label", "Test").rstrip("… ")
                if label.lower().endswith("running"):
                    label = label[:-len("running")].rstrip(" –-")
                self._notify(APP_TITLE, f"✓ {label} complete · {self._fmt_hms(elapsed)}")

    def _notify(self, title: str, message: str):
        """Best-effort macOS desktop notification with a sound."""
        try:
            import subprocess
            script = (f'display notification {json.dumps(message)} '
                      f'with title {json.dumps(title)} sound name "Glass"')
            subprocess.Popen(["osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _run_active_tab(self, _e=None):
        """Cmd+R — run the test on whichever tab is showing."""
        if self._busy:
            return
        runners = {
            "Connection": self.on_detect, "Benchmark": self.on_run_bench,
            "Optimum finder": self.on_run_optima, "Soak": self.on_run_soak,
            "Capacity": self.on_run_capacity,
            "Model fit": self.on_run_modelfit, "Provider fit": self.on_run_readiness,
            "Capabilities": self.on_run_capabilities, "Embed speed": self.on_run_embed,
            "Embed quality": self.on_run_embed_quality, "Vision": self.on_run_vision,
            "Network scan": self.on_scan,
        }
        try:
            current = self.tabview.get()
        except Exception:
            return
        # tabview.get() returns the (possibly translated) name; match via L().
        for en, fn in runners.items():
            if current == self.L(en):
                fn()
                return

    _PRESETS = {
        # Interactive chat: short prompts, short answers, modest concurrency.
        "Chat": {"var_tokens": "256", "var_ctx": "1024", "var_conc": "8", "var_reqs": "32",
                 "var_sweep": "1,2,4,8,16", "var_soak_conc": "32", "var_soak_in": "1024",
                 "var_soak_out": "256", "var_soak_dur": "5", "var_rd_in": "1024",
                 "var_rd_out": "256", "var_rd_sweep": "1,4,8,16,32"},
        # RAG: large context in, moderate output.
        "RAG": {"var_tokens": "512", "var_ctx": "8192", "var_conc": "8", "var_reqs": "32",
                "var_sweep": "1,2,4,8", "var_soak_conc": "16", "var_soak_in": "8000",
                "var_soak_out": "500", "var_soak_dur": "5", "var_rd_in": "8192",
                "var_rd_out": "256", "var_rd_sweep": "1,4,8,16"},
        # Agentic / batch: high concurrency, short structured output.
        "Agent": {"var_tokens": "384", "var_ctx": "2048", "var_conc": "32", "var_reqs": "64",
                  "var_sweep": "1,4,8,16,32,64", "var_soak_conc": "64", "var_soak_in": "2048",
                  "var_soak_out": "384", "var_soak_dur": "5", "var_rd_in": "2048",
                  "var_rd_out": "384", "var_rd_sweep": "1,8,16,32,64"},
    }

    def _apply_preset(self, name: str):
        preset = self._PRESETS.get(name)
        if not preset:
            return
        for var_name, value in preset.items():
            var = getattr(self, var_name, None)
            if var is not None:
                var.set(value)
        self._set_status(f"Applied '{name}' workload preset "
                         "(Benchmark / Soak / Provider fit).")

    # ------------------------------------------------------------- UI helpers
    def _set_busy(self, busy: bool, status: str = ""):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for name in ("btn_detect", "btn_models", "btn_run", "btn_scan",
                     "btn_opt", "btn_soak", "btn_capacity", "btn_fit", "btn_ready",
                     "btn_caps", "btn_embed", "btn_embq", "btn_vision"):
            b = getattr(self, name, None)
            if b is not None:
                b.configure(state=state)
        # Cancel buttons are the inverse: only usable while a task is running.
        for b in self._cancel_btns:
            b.configure(state="normal" if busy else "disabled")
        self.btn_repeat.configure(
            state="normal" if (not busy and self._last_run) else "disabled")
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
            if status:
                self._set_status(status)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self._set_status(self.t("ready"))

    def _set_status(self, text: str):
        self.status.configure(text=text)

    def _error(self, err: Exception):
        self._set_status(f"Error: {err}")
        for log in (getattr(self, "bench_log", None), getattr(self, "opt_log", None),
                    getattr(self, "soak_log", None), getattr(self, "capacity_log", None),
                    getattr(self, "embed_log", None), getattr(self, "fit_log", None)):
            if log is not None:
                log.write(f"✗ {err}", "err")
        messagebox.showerror(APP_TITLE, str(err))

    def _log_conn(self, text: str):
        self.conn_text.insert("end", text)
        self.conn_text.see("end")

    def _clear_comparison(self):
        self._cmp_data = []
        self._cmp_iid_test = {}
        for i in self.cmp_tree.get_children():
            self.cmp_tree.delete(i)
        self.bench_chart.clear()

    def add_comparison(self, test_type: str, history: list[dict]):
        if history:
            self._run_cfgs[test_type] = history[0]["config_hash"]
        if test_type not in self._run_order:
            self._run_order.append(test_type)
        self._cmp_data.append((test_type, history))
        self._render_comparison()

    def _render_comparison(self):
        cap = self.CMP_MAX_RUNS
        maxn = 1
        for _tt, hist in self._cmp_data:
            maxn = max(maxn, min(len(hist), cap))

        cols = ["metric"] + [f"r{i}" for i in range(maxn)]
        self.cmp_tree.configure(columns=cols)
        self.cmp_tree.heading("metric", text="Metric")
        self.cmp_tree.column("metric", width=200, minwidth=150, anchor="w", stretch=False)
        for i in range(maxn):
            self.cmp_tree.heading(f"r{i}", text="Latest" if i == 0 else f"−{i}")
            self.cmp_tree.column(f"r{i}", width=150, minwidth=110, anchor="w", stretch=False)

        for it in self.cmp_tree.get_children():
            self.cmp_tree.delete(it)
        self._cmp_iid_test = {}

        for gi, (tt, hist) in enumerate(self._cmp_data):
            hist = hist[:cap]
            runs = []
            for r in hist:
                try:
                    rows = json.loads(r["metrics_json"] or "{}").get("rows", [])
                    mp = {k: v for k, v in rows}
                    order = [k for k, _ in rows]
                except Exception:
                    mp, order = {}, []
                runs.append({"ts": r["ts"], "map": mp, "order": order})
            names, seen = [], set()
            for run in runs:
                for k in run["order"]:
                    if k not in seen:
                        seen.add(k)
                        names.append(k)

            if gi > 0:
                self.cmp_tree.insert("", "end", values=[""] * (maxn + 1))
            hid = self.cmp_tree.insert(
                "", "end", tags=("header",),
                values=[f"▾ {tt} — {len(hist)} run(s)"] + [""] * maxn)
            self._cmp_iid_test[hid] = tt
            wvals = ["when"] + [
                time.strftime("%m-%d %H:%M", time.localtime(runs[i]["ts"])) if i < len(runs) else ""
                for i in range(maxn)]
            wid = self.cmp_tree.insert("", "end", tags=("when",), values=wvals)
            self._cmp_iid_test[wid] = tt
            for ri, k in enumerate(names):
                vals = [k] + [
                    (runs[i]["map"].get(k, "") if i < len(runs) else "") for i in range(maxn)]
                iid = self.cmp_tree.insert("", "end", tags=("even" if ri % 2 else "odd",),
                                           values=vals)
                self._cmp_iid_test[iid] = tt

    # Metric prefixes the sweep emits (see the sweep block in _bench_job), mapped
    # to how they should be charted against concurrency.
    _SWEEP_METRICS = {
        "tok/s":        ("throughput vs concurrency", "tok/s"),
        "latency p95":  ("latency p95 vs concurrency", "s"),
        "latency p50":  ("latency p50 vs concurrency", "s"),
        "TTFT p95":     ("TTFT p95 vs concurrency", "s"),
    }

    def _on_cmp_select(self, _e=None):
        sel = self.cmp_tree.selection()
        if not sel:
            return
        test_type = self._cmp_iid_test.get(sel[0])
        cfg = self._run_cfgs.get(test_type)
        if not cfg:
            return
        if test_type == "sweep":
            # Chart whichever metric the clicked row belongs to (tok/s by default).
            name = (self.cmp_tree.item(sel[0], "values") or [""])[0]
            metric = next((m for m in self._SWEEP_METRICS if name.startswith(m)), "tok/s")
            self._plot_sweep_metric(self.bench_chart, cfg, metric)
        else:
            self._plot_config(self.bench_chart, test_type, cfg)

    def _plot_sweep_curve(self, chart, cfg_hash):
        self._plot_sweep_metric(chart, cfg_hash, "tok/s")

    def _plot_sweep_metric(self, chart, cfg_hash, metric):
        hist = store.history_for(cfg_hash, limit=1)
        if not hist:
            return
        try:
            rows = json.loads(hist[0]["metrics_json"] or "{}").get("rows", [])
        except Exception:
            rows = []
        prefix = metric + " @ c"
        pts = []
        for k, v in rows:
            if k.startswith(prefix):
                try:
                    c = int(k.split("@ c")[-1])
                    pts.append((c, float(str(v).split()[0])))
                except Exception:
                    pass
        pts.sort(key=lambda p: p[0])
        title, unit = self._SWEEP_METRICS.get(metric, ("vs concurrency", ""))
        chart.plot([(f"c{c}", val) for c, val in pts], title=title, unit=unit)

    def _record_and_compare(self, host, port, model, endpoint, test_type, params,
                            summary, rows, value=None, value_label=""):
        cfg = store.config_hash(host, port, model, endpoint, test_type, params)
        store.record_result(host=host, port=port, model=model, endpoint=endpoint,
                            test_type=test_type, cfg_hash=cfg, summary=summary,
                            metrics={"rows": rows, "summary": summary},
                            value=value, value_label=value_label)
        hist = store.history_for(cfg)
        self.post(lambda: self.add_comparison(test_type, hist))
        self.post(self.refresh_history)

    def _client(self) -> LLMClient:
        target = resolve_target(self.var_host.get(), self.var_port.get())
        return LLMClient.from_target(
            target,
            api_key=self.var_apikey.get().strip() or "EMPTY",
            timeout=float(self.var_timeout.get() or 95),
            endpoint=self.var_endpoint.get(),
        )

    def _resolved_model(self) -> str | None:
        m = self.var_model.get().strip()
        return m or None

    # ------------------------------------------------------------- Connection
    def on_detect(self):
        raw_host = self.var_host.get().strip()
        raw_port = self.var_port.get().strip()
        if not raw_host:
            return self._error(ValueError("Enter a host, URL, or host:port first."))
        target = resolve_target(raw_host, raw_port)
        self._log_conn(f"\n→ Detecting {raw_host} … (trying {target.base_url})\n")
        self.run_async(smart_detect(raw_host, raw_port, timeout=5.0), self._show_detect,
                       status=f"Detecting {raw_host} …")

    def _show_detect(self, s):
        self._log_conn(
            f"   resolved:  {s.url}\n"
            f"   reachable: {s.reachable}\n"
            f"   type:      {s.server_type}\n"
            f"   OpenAI API:{'yes' if s.openai_compatible else 'no'}\n"
            f"   version:   {s.version or '-'}\n"
            f"   models:    {', '.join(s.models) if s.models else '-'}\n"
            + (f"   {s.note}\n" if s.note else "")
        )
        if s.reachable:
            # Pin the resolved scheme/port back into the fields so benchmarks
            # reuse exactly what was found (the URL form round-trips losslessly).
            self.var_host.set(s.url)
            self.var_port.set(str(s.port))
            self._remember_endpoint(s.host, s.port)
        if s.models:
            self.model_combo.configure(values=s.models)
            if not self.var_model.get():
                self.var_model.set(s.models[0])
        self._set_status(f"Detected: {s.server_type} ({len(s.models)} model(s))")

    def on_list_models(self):
        try:
            client = self._client()
        except ValueError as e:
            return self._error(e)
        self._remember_endpoint(self.var_host.get().strip(), self.var_port.get().strip())
        self.run_async(client.list_models(), self._show_models, status="Listing models…")

    def _show_models(self, models):
        self.model_combo.configure(values=models or [""])
        if models and not self.var_model.get():
            self.var_model.set(models[0])
        self._log_conn(f"\n→ Models ({len(models)}): {', '.join(models) if models else '-'}\n")
        self._set_status(f"{len(models)} model(s) found.")

    # -------------------------------------------------------------- Benchmark
    def on_run_bench(self):
        try:
            target = resolve_target(self.var_host.get(), self.var_port.get())
            snap = {
                "host": target.host,
                "port": target.port,
                "scheme": target.scheme,
                "base_path": target.base_path,
                "api_key": self.var_apikey.get().strip() or "EMPTY",
                "endpoint": self.var_endpoint.get(),
                "timeout": float(self.var_timeout.get() or 95),
                "model": self._resolved_model(),
                "params": {
                    "tokens": int(self.var_tokens.get()),
                    "ctx": int(self.var_ctx.get()),
                    "runs": int(self.var_runs.get()),
                    "concurrency": int(self.var_conc.get()),
                    "requests": int(self.var_reqs.get()),
                    "sweep_levels": self._parse_levels(self.var_sweep.get(), [1, 2, 4, 8, 16]),
                    "ctx_probe": int(self.var_ctxprobe.get()),
                },
                "flags": {
                    "speed": self.t_speed.get(),
                    "load": self.t_load.get(),
                    "ctx": self.t_ctx.get(),
                    "sanity": self.t_sanity.get(),
                    "sweep": self.t_sweep.get(),
                    "prefix": self.t_prefix.get(),
                    "determinism": self.t_determ.get(),
                    "limits": self.t_limits.get(),
                },
            }
        except ValueError as e:
            return self._error(ValueError(f"Invalid number: {e}"))
        if not any(snap["flags"].values()):
            return self._error(ValueError("Select at least one test."))
        self._launch_bench(snap)

    def on_repeat(self):
        if not self._last_run:
            return self._set_status("No previous run to repeat.")
        self._launch_bench(self._last_run)

    @staticmethod
    def _parse_levels(spec: str, default: list[int]) -> list[int]:
        out = []
        for p in spec.split(","):
            p = p.strip()
            if p.isdigit() and int(p) > 0:
                out.append(int(p))
        return out or default

    def _launch_bench(self, snap: dict):
        self._last_run = snap
        self._remember_endpoint(snap["host"], snap["port"])
        client = LLMClient(snap["host"], snap["port"], api_key=snap["api_key"],
                           timeout=snap["timeout"], endpoint=snap["endpoint"],
                           scheme=snap.get("scheme", "http"),
                           base_path=snap.get("base_path", ""))
        self._run_cfgs = {}
        self._run_order = []

        def status(msg):
            self.post(lambda m=msg: (self._set_status(m), self.bench_log.write(m, "head")))

        self._clear_comparison()
        self.bench_log.clear()
        n_tests = sum(1 for v in snap["flags"].values() if v)
        self.bench_log.write(f"▶ Benchmark start · {n_tests} test(s)", "head")
        self.bench_log.write(f"        target: {client.base_url}", "dim")
        self.bench_log.write(f"        model:  {snap['model'] or '(auto)'}", "dim")
        self.run_async(
            self._bench_job(client, snap["model"], snap["params"], snap["flags"], status),
            self._bench_done, status="Running benchmark…")

    def _bench_done(self, _res):
        self._set_status("Benchmark complete.")
        self.bench_log.write("✓ Benchmark complete", "ok")
        for tt in self._run_order:
            cfg = self._run_cfgs.get(tt)
            if not cfg:
                continue
            if tt == "sweep":
                self._plot_sweep_curve(self.bench_chart, cfg)
            else:
                self._plot_config(self.bench_chart, tt, cfg)
            break

    async def _bench_job(self, client, model, params, flags, status):
        import statistics as st

        if not model:
            try:
                models = await client.list_models()
                model = models[0] if models else None
            except Exception:
                model = None
            if not model:
                raise RuntimeError("No model specified and model listing failed.")
            status(f"Auto-selected model: {model}")

        def record(test_type, summary, rows, value=None, value_label=""):
            self._record_and_compare(client.host, client.port, model, client.endpoint,
                                     test_type, params, summary, rows, value, value_label)
            self.post(lambda: self.bench_log.result(test_type, summary, list(rows)))

        if flags["sanity"]:
            status("Sanity test…")
            r, passed, expected, got = await B.sanity(client, model)
            rows = [
                ("passed", "✅ yes" if passed else "❌ no"),
                ("expected", expected),
                ("got", (got[:60] + "…") if len(got) > 60 else (got or "-")),
                ("error", r.error or "-"),
            ]
            if passed:
                sm = "✅ pass"
            else:
                detail = got[:40] if got else (r.error or "empty response")
                sm = f"❌ fail ({detail})"
            record("sanity", sm, rows, value=1.0 if passed else 0.0, value_label="pass (1=ok)")

        if flags["speed"]:
            status("Latency test…")
            r = await B.latency(client, model, max_tokens=params["tokens"])
            if r.ok:
                rows = [
                    ("TTFT (s)", f"{r.ttft:.3f}"),
                    ("total (s)", f"{r.total_time:.3f}"),
                    ("prompt tokens", str(r.prompt_tokens)),
                    ("completion tokens", str(r.completion_tokens)),
                    ("decode tok/s", f"{r.output_tps:.1f}"),
                ]
                summary = f"{r.output_tps:.0f} tok/s · TTFT {r.ttft:.2f}s · {r.total_time:.2f}s total"
            else:
                rows = [("error", r.error)]
                summary = f"error: {r.error[:40]}"
            record("latency", summary, rows,
                   value=(r.output_tps if r.ok else None), value_label="decode tok/s")

            status(f"Throughput test ({params['runs']} runs)…")
            results, ok = await B.throughput(client, model, max_tokens=params["tokens"],
                                             runs=params["runs"])
            if ok:
                tps = st.mean(x.output_tps for x in ok)
                ttft = st.mean(x.ttft for x in ok)
                rows = [
                    ("decode tok/s", f"{tps:.1f}"),
                    ("TTFT (s)", f"{ttft:.3f}"),
                    ("completion tokens", f"{st.mean(x.completion_tokens for x in ok):.0f}"),
                    ("successful runs", f"{len(ok)}/{len(results)}"),
                ]
                summary = f"{tps:.0f} tok/s avg · TTFT {ttft:.2f}s · {len(ok)}/{len(results)} runs"
            else:
                err = results[0].error if results else "all failed"
                rows = [("error", err)]
                summary = f"error: {err[:40]}"
            record("throughput", summary, rows,
                   value=(st.mean(x.output_tps for x in ok) if ok else None),
                   value_label="decode tok/s")

        if flags["load"]:
            status(f"Load test (c={params['concurrency']}, n={params['requests']})…")
            prog = lambda d, t: self.post(lambda: self._set_status(f"Load test… {d}/{t}"))
            stt = await B.load(client, model, concurrency=params["concurrency"],
                               requests=params["requests"], max_tokens=params["tokens"],
                               progress_cb=prog)
            rows = [
                ("success", f"{stt.success}/{stt.requests}"),
                ("wall time (s)", f"{stt.wall_time:.2f}"),
                ("request throughput (req/s)", f"{stt.req_per_s:.2f}"),
                ("in tok/s (prefill)", f"{stt.input_tps:.1f}"),
                ("out tok/s (decode)", f"{stt.aggregate_tps:.1f}"),
                ("total tok/s", f"{stt.total_tps:.1f}"),
                ("peak out tok/s (1s window)", f"{stt.peak_out_tps:.1f}"),
                ("TPOT (ms/token, excl 1st)",
                 f"{stt.tpot_ms:.2f}" if stt.tpot_ms > 0 else "– (server not streaming)"),
                ("TTFT p50 / p95 (s)", f"{stt.ttft_p50:.3f} / {stt.ttft_p95:.3f}"),
                ("latency p50 / p95 (s)", f"{stt.latency_p50:.2f} / {stt.latency_p95:.2f}"),
            ]
            if stt.est_frac >= 0.5:
                rows.append(("⚠ token counts", f"estimated (~4 chars/tok) — server sent no usage; "
                                               "tok/s approximate"))
            for i, e in enumerate(stt.errors[:3]):
                rows.append((f"error {i + 1}", e[:60]))
            summary = (f"out {stt.aggregate_tps:.0f} / in {stt.input_tps:.0f} tok/s · "
                       f"p95 {stt.latency_p95:.2f}s · {stt.success}/{stt.requests} ok")
            record("load", summary, rows,
                   value=stt.aggregate_tps, value_label="agg tok/s")

        if flags["ctx"]:
            status(f"Context / prefill test (~{params['ctx']} tok)…")
            r, prefill = await B.context_test(client, model, ctx_tokens=params["ctx"])
            if r.ok:
                rows = [
                    ("prompt tokens", str(r.prompt_tokens)),
                    ("TTFT (s)", f"{r.ttft:.3f}"),
                    ("prefill tok/s", f"{prefill:.0f}"),
                    ("decode tok/s", f"{r.output_tps:.1f}"),
                ]
                summary = f"prefill {prefill:.0f} tok/s · {r.prompt_tokens} ptok · TTFT {r.ttft:.2f}s"
            else:
                rows = [("error", r.error)]
                summary = f"error: {r.error[:40]}"
            record("context", summary, rows,
                   value=(prefill if r.ok else None), value_label="prefill tok/s")

        if flags.get("sweep"):
            levels = params["sweep_levels"]
            prog = lambda c, last: self.post(
                lambda: self._set_status(f"Concurrency sweep… c={c}/{last}"))
            points = await B.concurrency_sweep(client, model, levels=levels,
                                               max_tokens=params["tokens"], progress_cb=prog)
            # One block of tok/s rows, then latency rows — so throughput and how
            # latency degrades under load are both visible (and chartable). The
            # "@ cN" keys are what _plot_sweep_metric parses back into curves.
            rows = []
            for p in points:
                rows.append((f"tok/s @ c{p.concurrency}",
                             f"{p.agg_tps:.1f}  ({p.success}/{p.requests} ok)"))
            for p in points:
                rows.append((f"latency p95 @ c{p.concurrency}", f"{p.lat_p95:.2f} s"))
            for p in points:
                rows.append((f"latency p50 @ c{p.concurrency}", f"{p.lat_p50:.2f} s"))
            for p in points:
                rows.append((f"TTFT p95 @ c{p.concurrency}", f"{p.ttft_p95:.3f} s"))
            best = max(points, key=lambda p: p.agg_tps) if points else None
            if best:
                worst_lat = max(points, key=lambda p: p.lat_p95)
                rows.append(("best concurrency", str(best.concurrency)))
                rows.append(("peak tok/s", f"{best.agg_tps:.1f}"))
                rows.append(("latency @ peak", f"{best.lat_p95:.2f} s p95"))
                rows.append(("worst latency p95", f"{worst_lat.lat_p95:.2f} s @ c{worst_lat.concurrency}"))
                summary = (f"peak {best.agg_tps:.0f} tok/s @ c{best.concurrency} · "
                           f"lat p95 {best.lat_p95:.2f}→{worst_lat.lat_p95:.2f}s")
                value = best.agg_tps
            else:
                summary, value = "no data", None
            record("sweep", summary, rows, value=value, value_label="peak tok/s")

        if flags.get("prefix"):
            status("Prefix-cache test…")
            d = await B.prefix_cache_test(client, model, prefix_tokens=params["ctx"])
            if d["ok"]:
                rows = [
                    ("cold TTFT (s)", f"{d['cold_ttft']:.3f}"),
                    ("warm TTFT (s)", f"{d['warm_ttft']:.3f}"),
                    ("speedup ×", f"{d['speedup']:.2f}"),
                    ("prefix caching", "✅ likely on" if d["likely"] else "❌ not detected"),
                    ("prompt tokens", str(d["prompt_tokens"])),
                ]
                summary = (f"{d['speedup']:.2f}× speedup · "
                           f"{'likely on' if d['likely'] else 'not detected'}")
                value = d["speedup"]
            else:
                rows = [("error", d["error"])]
                summary, value = f"error: {d['error'][:40]}", None
            record("prefix", summary, rows, value=value, value_label="cache speedup ×")

        if flags.get("determinism"):
            status("Determinism test…")
            d = await B.determinism_test(client, model, runs=params["runs"],
                                         max_tokens=params["tokens"])
            rows = [
                ("valid runs", f"{d['valid']}/{d['runs']}"),
                ("unique outputs", str(d["unique"])),
                ("% identical", f"{d['pct']:.0f}"),
                ("deterministic", "✅ yes" if d["deterministic"] else "❌ no"),
            ]
            summary = ("deterministic" if d["deterministic"]
                       else f"{d['unique']} variants") + f" · {d['pct']:.0f}% identical"
            record("determinism", summary, rows,
                   value=(d["pct"] if d["valid"] else None), value_label="% identical")

        if flags.get("limits"):
            status("Probing max context…")
            probe = await B.context_limit_probe(client, model, low=256,
                                                high=params["ctx_probe"])
            mx = probe["tokens"] or probe["approx"]
            src = probe.get("source", "probed")
            nctx = min(params["ctx"], int(mx * 0.9)) if mx else params["ctx"]
            status("Needle-in-haystack…")
            needle = await B.needle_test(client, model, ctx_tokens=nctx)
            rows = [("max ctx (tokens)", f"{mx}  ({src})")]
            for x in needle["results"]:
                rows.append((f"recall @ {int(x['depth'] * 100)}% depth",
                             "✅ pass" if x["passed"] else f"❌ ({x['got'] or x['error'] or 'miss'})"))
            rows.append(("recall passed", f"{needle['passed']}/{needle['total']}"))
            summary = f"max ctx {mx} tok ({src}) · recall {needle['passed']}/{needle['total']}"
            record("limits", summary, rows,
                   value=(float(mx) if mx else None), value_label="max ctx (tokens)")

    # ----------------------------------------------------------------- Scan
    def on_scan(self):
        try:
            subnet = self.var_subnet.get().strip()
            ports = parse_ports(self.var_ports.get(), DEFAULT_PORTS)
            timeout = float(self.var_scan_timeout.get())
            conc = int(self.var_scan_conc.get())
        except ValueError as e:
            return self._error(ValueError(f"Invalid scan parameter: {e}"))
        for i in self.scan_tree.get_children():
            self.scan_tree.delete(i)
        self._scan_holder = {"done": 0, "total": 1, "phase": "scan"}
        self.scan_progress.set(0)
        self.run_async(self._scan_job(subnet, ports, timeout, conc), self._show_scan,
                       status=f"Scanning {subnet}…")
        self._poll_scan()

    async def _scan_job(self, subnet, ports, timeout, conc):
        def cb(done, total):
            self._scan_holder["done"] = done
            self._scan_holder["total"] = total
        pairs = await scan_network(subnet, ports, timeout=timeout, concurrency=conc,
                                   progress_cb=cb)
        self._scan_holder["phase"] = "detect"
        servers = await detect_many(pairs, timeout=4.0)
        return pairs, servers

    def _poll_scan(self):
        if not self._busy:
            return
        h = self._scan_holder
        if h["phase"] == "detect":
            self._set_status("Open ports found — identifying servers…")
            self.scan_progress.set(1.0)
        else:
            total = max(h["total"], 1)
            self.scan_progress.set(min(h["done"] / total, 1.0))
            self._set_status(f"Scanning… {h['done']}/{total} probes")
        self.root.after(150, self._poll_scan)

    def _show_scan(self, payload):
        pairs, servers = payload
        for n, s in enumerate(sorted(servers, key=lambda x: (x.host, x.port))):
            models = ", ".join(str(m) for m in s.models[:4]) + ("…" if len(s.models) > 4 else "")
            self.scan_tree.insert("", "end", tags=("even" if n % 2 else "odd",), values=(
                s.host, s.port, s.server_type,
                "yes" if s.openai_compatible else "no", models))
        self.scan_progress.set(1.0)
        self._set_status(f"Scan done: {len(servers)} LLM server(s) of {len(pairs)} open port(s).")
        if not servers and not pairs:
            messagebox.showinfo(APP_TITLE, "No open ports found on this subnet.")

    def on_use_server(self):
        sel = self.scan_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "Select a server in the list first.")
            return
        vals = self.scan_tree.item(sel[0], "values")
        host, port = vals[0], vals[1]
        self.var_host.set(host)
        self.var_port.set(str(port))
        self._remember_endpoint(host, port)
        self._set_status(f"Using {host}:{port} — go to Connection/Benchmark tabs.")

    # ----------------------------------------------------------------- close
    def _on_close(self):
        try:
            self.runner.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    # Restore the saved theme (System follows the OS light/dark setting).
    mode = store.get_setting("appearance", "System")
    ctk.set_appearance_mode(mode if mode in ("System", "Light", "Dark") else "System")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
