# LLM Scanner

A Mac-friendly client (Python) for **discovering and testing local AI language-model
servers**. Works with any OpenAI-compatible server:

- **vLLM** (port 8000)
- **SGLang** (port 30000)
- **llama.cpp server** (port 8080)
- **Ollama** (port 11434)
- **TGI** (text-generation-inference)
- **LM Studio** (port 1234), **LocalAI**, **koboldcpp**, etc.

The app has a **modern graphical window** (CustomTkinter — dark/light theme, no heavy Qt
install needed) and, as a bonus, a command-line interface for scripting.

## Features

- 🔌 **Connect to a server** – enter host, port, API key, pick the endpoint (`chat`/`completions`) and model.
- 🧠 **Smart host field** – the Host field accepts a bare hostname (`api.example.com`), a `host:port`
  form, or a full URL (`https://host/v1`). The scheme (http/https), port and any path prefix are
  derived automatically: a public domain goes to HTTPS (443), a local IP/localhost to HTTP (using
  the Port field). See [Smart host field](#smart-host-field).
- 🔎 **Detect server** – "Detect server" tries several candidates itself (https→http) and picks the
  first that answers; it fingerprints the server type (vLLM/SGLang/Ollama/…), lists the models and
  writes the resolved URL back into the host field.
- 🌐 **Network scan** – scans the local subnet (e.g. `192.168.1.0/24`) and finds running LLM servers.
- 🎯 **Optimum finder** – a separate tab that **automatically finds the optimal concurrency and the
  largest workable request size**. See [Optimum finder](#optimum-finder).
- ⏳ **Soak test** – holds a fixed load for N minutes and measures **sustained tokens in/out per
  hour** (and whether throughput stays stable). Supports replaying a **real production
  workload mix**. See [Soak test](#soak-test).
- 🚀 **Capacity** – a separate tab that **ramps concurrency in steps (1→2→4→…)** and finds the **peak
  sustained tokens/minute** — the endpoint's capacity **ceiling** and its saturation point. Optional
  **Target tok/min → PASS/FAIL**. See [Capacity](#capacity-tokmin-ceiling).
- 🧪 **Model fit (Openclaw / Hermes)** – a separate tab that judges **whether a model is suitable for
  agentic use**: Hermes tool calls, structured JSON, instruction following → verdict
  FIT / BORDERLINE / NOT FIT. See [Model fit](#model-fit-agentic-suitability).
- 🔌 **Provider fit (OpenRouter / HuggingFace)** – a separate tab that checks **whether a backend can
  take real router traffic**: API-contract compliance (streaming, usage accounting, max_tokens/stop,
  determinism, sampling parameters, clean error codes) plus a concurrency sweep that finds the
  throughput **knee and the first bottleneck** (prefill/queue, decode, batching, admission control).
  A FIT / BORDERLINE / NOT FIT verdict per provider. See
  [Provider fit](#provider-fit-openrouter--huggingface).
- 🧩 **Capabilities** – a separate tab that **discovers what an endpoint/model actually offers**: API
  routes (**embeddings**, rerank, tokenize, moderations, audio, images) and chat features (streaming,
  tool calls, JSON mode, vision, logprobs, seed, reasoning). Each row = one quick probe → ✓ / ✗ / ~ /
  n/a. See [Capabilities](#capabilities-what-the-endpoint-offers).
- 🚄 **Embed speed** – a separate tab that **measures an embedding model's throughput and latency**:
  it holds a batch load and reports **embeddings/s, input tokens/s, req/s and latency** (p50/p95).
  Batch size is its own knob (embedding servers batch very efficiently). See
  [Embed speed](#embed-speed-embedding-throughput).
- 🎯 **Embed quality** – a separate tab that **checks whether the embeddings actually work** (not how
  fast): retrieval ranking, paraphrase-vs-unrelated similarity, **Estonian↔English** cross-lingual,
  vector properties (L2 norm, determinism, dim), input/batch limits and rerank relevance. Each row is
  ✓/✗/~ plus the numbers. See [Embed quality](#embed-quality-do-the-embeddings-work).
- 👁️ **Vision (VL)** – a separate tab that **checks whether a VL model really understands images**: it
  sends generated images with known content (colours, text/number, squares, several images at once)
  and compares the answer against the truth — **colour recognition, OCR, counting, multi-image
  reasoning**. See [Vision](#vision-vl--does-the-model-understand-images).
- 📊 **Tests** (Benchmark tab):
  - **Speed** – latency (TTFT, time to first token) + throughput (decode tokens/s).
  - **Load test** – N concurrent requests; aggregate tok/s and p50/p95 latency.
  - **Context / prefill** – sends a long ~ctx-length prompt and measures prefill speed; also checks that the ctx fits.
  - **Sanity** – a simple correctness check (not just speed).
  - **Concurrency sweep** – walks the concurrency levels (e.g. 1,2,4,8,16) and finds the saturation
    point. At every level it measures both tok/s and **how latency moves** (p50/p95, TTFT); the chart
    plots tok/s **or** latency vs concurrency (pick the matching row in the table).
  - **Prefix cache** – sends the same long prefix twice and measures the TTFT speed-up → detects automatic prefix caching (vLLM/SGLang).
  - **Determinism** – the same prompt at temp=0 N times → what % of outputs are identical (exposes batching non-determinism).
  - **Limits + recall** – binary-searches the real maximum context length + needle-in-a-haystack (hides a code in a long context and asks for it back).
- 🪵 **Live log** – on the Benchmark and Optimum tabs it streams the test's progress in real time (on
  the right, behind a draggable splitter): every phase and every finished result with its concrete
  metric lines and a timestamp (green = OK, red = error).
- ⚙️ Configurable: max output tokens, context tokens, concurrency, request count, **timeout (default 95 s)**.
- 💾 **Saved hosts** – save a host with all of its parameters and **quick-select** it later from the drop-down (Connection tab → "Saved hosts").
- ⌨️ **Host/port autocomplete** – previously used IPs and ports are remembered and appear in the Host and Port drop-downs; picking an IP fills in that host's last-used port automatically.
- 📚 **Every result is kept** – each test is stored in a database (`~/.llmscanner/llmscanner.db`). A separate **History** tab shows the whole history; Export CSV and Clear buttons included.
- 🔁 **Side-by-side re-run comparison** – re-run the same test against the same host with the same config and you get a matrix with **runs as columns** (newest on the left: Latest, −1, −2 …) and metrics as rows. Scroll right for older runs.
- 🎨 **Comfortable tables** – zebra striping; **columns can be reordered by dragging** the header and
  **resized** by dragging the border. In the History table, clicking a header also sorts.
- 📈 **Chart** – tok/s (or another metric) over time as a line (a lightweight Canvas chart, no extra dependency). On the Benchmark tab pick a row in the comparison table to plot it; on the History tab pick a result row to see that config's series over time.
- ⏯️ **Repeat last run** – a button that re-runs the last benchmark with exactly the same config (ideal for collecting a series for the chart).
- 📤 **Export / Copy** – on both the Benchmark and Optimum tabs the results can be **saved as CSV** or
  **copied to the clipboard** (tab-separated, pastes straight into a spreadsheet) — headers included.
- ❓ **Info icons** – next to every setting (on every tab) there is an **ⓘ** that opens an explanation
  of what the parameter does and what different values give you.
- ❔ **Help** – a button at the top right that opens the guide (a tour of the tabs + tips) and the
  **infrastructure and support contacts**.
- 🌗 **Theme** – **System / Light / Dark** at the top right; applies immediately and is remembered.
- 🌐 **Language** – **English (primary) and Estonian**; the choice is remembered and applies
  immediately. Tab names, section titles, field labels, buttons and checkboxes are translated; the
  ⓘ help texts and long descriptions stay English (fallback).
- ⚡ **Load presets** – on the Connection tab the **Chat / RAG / Agent** buttons fill sensible
  parameters into the Benchmark, Soak and Provider-fit tabs in one click.
- 🆚 **Cross host/model comparison** – on the History tab select several rows (Cmd/Shift-click) →
  **"Compare selected"** gives a side-by-side table (metrics × runs) across different servers,
  models and configs.
- 🧾 **Shareable report** – **"Export report"** saves the selected run(s) as a Markdown/HTML file
  (metadata + a metrics table; several selected → a comparison table).
- 🔔 **Completion notifications and shortcuts** – a macOS notification plus a sound when a long test
  finishes; Cmd+R (run the active tab's test), Cmd+. / Esc (stop), Cmd+D (detect), Cmd+L (models).
- 🖥️ **The window fits the screen** – the window (and the Help/Comparison windows) is sized from the
  screen's dimensions and centred, so the app never opens larger than a smaller display.

## Installation (macOS)

Python 3.9+ is required (tested on 3.13). Tkinter usually ships with Python.

```bash
cd ~/llmscanner

# Recommended: a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# For the GUI:
pip install httpx customtkinter

# Optionally the command-line tool (needs rich):
pip install rich

# or install the package (gives you the `llmscanner` and `llmscanner-cli` commands):
pip install -e '.[cli]'
```

> **No Tkinter?** CustomTkinter is built on Tkinter. If you use Homebrew Python,
> run `brew install python-tk`. The python.org installer already includes Tk.

## Running

**The graphical app:**

```bash
python -m llmscanner
# or, if the package is installed:
llmscanner
# or with no prior steps (creates the venv + installs on first run):
./run.sh
```

**A standalone macOS executable (no Python needed):**

```bash
./build_macos.sh          # builds dist/LLMScanner — one file, bundles Python + Tk
open dist/LLMScanner      # run it (or double-click in Finder)
```

`build_macos.sh` uses PyInstaller's `--onefile` mode and bundles the customtkinter
themes; the icon is generated programmatically (no images need to be shipped).

**Command line (bonus):**

```bash
# Scan the local network
llmscanner-cli scan
llmscanner-cli scan --subnet 192.168.1.0/24 --ports 8000,8080,30000,11434

# Detect a single server
llmscanner-cli detect --host 127.0.0.1 --port 8000

# List models
llmscanner-cli models --host 127.0.0.1 --port 8000

# Run tests
llmscanner-cli bench --host 127.0.0.1 --port 8000 --tokens 256 --test all
llmscanner-cli bench --host 10.0.0.5 --port 30000 --model my-model \
    --test load --concurrency 16 --requests 64
```

## Smart host field

The host field no longer has to be a bare IP — the app works out how to reach the server itself:

| You type | The app uses |
|---|---|
| `api.example.com` | `https://api.example.com` (443) — a public domain → HTTPS |
| `https://api.example.com/v1` | the same; the `/v1` suffix is stripped automatically |
| `api.example.com:9000` | `https://api.example.com:9000` |
| `127.0.0.1` / `localhost` | `http://…:<port field>` — local → HTTP + the port field |
| `192.168.1.5:8080` | `http://192.168.1.5:8080` |
| `http://host/api` | the reverse-proxy path prefix `/api` is kept (requests go to `/api/v1/...`) |
| `[::1]:8000` | `http://[::1]:8000` — IPv6 in brackets |

**The rules in short:** a scheme/port/path inside the host string always beats the port field. A bare
public domain defaults to HTTPS (443) — the port field is meant for the local-server workflow. A local
IP / `localhost` uses HTTP on the port field.

**TLS verification** is skipped only for a private or loopback host, where a self-signed certificate
is the norm. Against a public host it stays **on**: every request carries your API key in an
`Authorization` header, and an unverified connection would expose that key to anyone able to
intercept it. Set `LLMSCANNER_INSECURE_TLS=1` if you really do need to reach a public host with a
self-signed certificate.

**"Detect server"** does not assume the right scheme: it tries the derived candidates in order
(e.g. `https://host` → `http://host` → `http://host:<port>`) and picks the first that answers; the
resolved URL is written back into the host field so the following tests use exactly that.

## Optimum finder

The separate **Optimum finder** tab is an automatic tuning tool: it works out **how many concurrent
requests** and **how large a request** it is worth giving the model at once. It uses the Connection
tab's host, model and timeout. The work is split into phases (A–B always, C–E optional):

1. **Phase A — max context.** Finds the largest single request that succeeds (up to a configurable
   ceiling, 65536 tokens by default — set it to match the server's `--max-model-len`). It prefers the
   server's reported `max_model_len` (vLLM), otherwise it reads the limit out of the error message or
   binary-searches — so as few oversized requests as possible are sent.
2. **Phase B — concurrency sweep.** Walks the concurrency levels (e.g. 1→128) at a moderate context
   and **stops early** once throughput plateaus or the level starts erroring. It reports:
   - the **peak-throughput** concurrency (max aggregate tok/s);
   - the **efficiency knee** — the lowest concurrency that still delivers ≥90% of the peak (the
     practical optimum, because latency there is below the peak's). This is the **recommended** value.
3. **Phase C — request-size sweep** (optional). Walks the chosen request sizes (by default
   **1024–65536** tokens: `1024,2048,4096,8192,16384,32768,49152,65536`) and finds, **at each size**,
   both the optimal (peak-throughput) concurrency and the highest concurrency that still works. Sizes
   above the detected max context are skipped (and marked). This exposes the **KV-cache trade-off**:
   with a bigger request you can run fewer at once (e.g. `1k → best c16, max c64;  4k → best c16, max c16`).

4. **Phase D — generation-length sweep** (optional, off by default). Walks the chosen output lengths
   (by default **64, 256, 1024** tokens) **at the recommended concurrency (the knee)** and shows how
   output length affects decode throughput (out tok/s) and latency. Toggled with the "Sweep generation
   lengths" checkbox.
5. **Phase E — workload profiles** (optional, off by default). Measures fixed (input/output) workloads
   at one concurrency — following the standard vLLM serving benchmark: **prompt-heavy 8000/1000,
   decode-heavy 1000/8000, balanced 1000/1000** (defaults, editable), concurrency **16**. That makes
   the results **directly comparable** with published numbers (e.g. NVIDIA DGX Spark / Blackwell /
   Jetson Thor). Toggled with "Workload profiles". *NB: a profile with a large output (e.g. 8000) can
   exceed the timeout on a slower server — raise Timeout then.*

The size list is editable on the Optimum tab ("Request sizes (tok)"). The sizes are the **input
(prompt) size** — that is what drives KV-cache pressure; the generated answer is kept small so the
measurement is specifically about how many requests of that input size the server can take at once.

### The "Gen tokens / req" parameter

This is the **maximum number of output (answer) tokens per request** — how long the model generates
for each request (the decode length). The effect:

- **Small** (e.g. 32–64) → the test is fast, but with a large prompt the time goes mostly into
  prefill, so **out tok/s** reflects decode poorly.
- **Large** (e.g. 512+) → measures **real decode speed**, but requests take longer (a slower test,
  and easier to hit the timeout at high concurrency/context).

Phases B and C use a single value (the "Gen tokens / req" field). To see the **effect of different
output lengths**, switch on **Phase D** ("Sweep generation lengths") and give it a list
("Gen lengths (tok)").

### The three throughput metrics (in / out / total tok/s)

All three are **aggregate** rates over the same batch's wall time, so **In + Out = Total** (the same
decomposition the vLLM benchmark uses):

- **In tok/s** = `input (prompt) tokens / wall` — how fast the server **ingests input** (prefill).
  With a large context this is the headline number.
- **Out tok/s** = `output (completion) tokens / wall` — how fast **output is produced** (decode). With
  a large prompt and a short answer this looks small, because the wall time goes mostly into prefill —
  that is not a bug, the metric simply does not count input tokens.
- **Total tok/s** = `(input + output) / wall` — all the work. **The finder ranks (peak/knee) by this**,
  because it reflects the server's real throughput.

In addition (as in the vLLM benchmark):

- **TPOT (ms/token)** = `(latency − TTFT) / (output tokens − 1)` — **time per output token**
  (excluding the first), i.e. pure decode latency. This is the **most comparable** decode metric:
  independent of prefill, stable across input/output sizes. **It requires token-by-token streaming** —
  the client asks for `stream:true` + `Accept: text/event-stream`. If the gateway **buffers** the
  response anyway (TTFT = the whole latency), TPOT is not measurable and is shown as **"–"** (not 0),
  with a warning in the recommendation.
- **req/s** = successful requests / wall — request throughput.
- **peak out tok/s** = the most output tokens in any 1-second window (completion-based — accurate for
  a buffering server; with a streaming server, requests finishing together can overstate the peak).

**Fixed output length (for comparability):** the load and optimum tests send `ignore_eos` +
`min_tokens`, so **every request decodes exactly `max_tokens` tokens**. Without that the model would
often stop after ~1 token (especially with a large filler prompt), which would make Out tok/s almost
zero and Total merely prefill throughput — the rows would not be comparable. (If the server does not
support those fields they are dropped automatically.) NB: because Total includes input tokens it grows
naturally with context size — to compare different context sizes look at **Out tok/s**, or compare
within the same size.

> Token counts come from the server's `usage` field (exact on vLLM/SGLang), otherwise they are
> **estimated** (~4 characters per token). If a server omits `usage` (a silent semantic degradation —
> the parameter disappears but the response is 200 OK), **all tok/s numbers are approximate** — the
> tool marks such rows **⚠ est tokens** and adds a warning to the recommendation (column `est_frac`
> in the export). A per-request **decode rate** is also computed
> (output / (end − first token), without prefill) — on the Benchmark tab's load test, the row
> "per-req decode tok/s (mean)".

### Distinct prefixes (avoiding prefix-affinity routing)

Some load balancers (e.g. **LLMRouter**) use **prefix-affinity routing**: requests with a
similar/identical prompt prefix are sent to **the same backend** for KV-cache warmth. In a synthetic
load test that would mean all "N concurrent users" land on **one GPU** while the others idle → the
concurrency measurement comes out wrong (too low).

To avoid that, the optimum finder starts every request with a **unique high-entropy preamble**
(~64 random tokens) that differs from the very first token and is long enough to cover the router's
prefix blocks. The router then sees each request as **its own conversation** and spreads the load
across backends.

Toggled with the **"Distinct request prefixes"** checkbox (**on** by default). Turn it off when you
deliberately want to measure prefix-cache / affinity behaviour (requests will then most likely go to
a single backend).

### Export CSV / Copy to clipboard

The **"Export CSV…"** button saves every measured point to a CSV file with **column headers**:
`phase, concurrency, ctx_tokens, gen_tokens, requests, success, in_tok_s, out_tok_s, total_tok_s,
tpot_ms, req_per_s, peak_out_tok_s, lat_p50_s, lat_p95_s, ttft_p95_s, feasible, note`.

The **"Copy to clipboard"** button puts the same table (headers included) on the clipboard as
**tab-separated** text, which pastes straight into a spreadsheet (Excel/Sheets) as columns.

Every measured point appears in the table in real time (green = feasible, red = failed, peak/knee
highlighted) and in the live log; at the end you get a **recommendation sentence** and a
tok/s-vs-concurrency chart. A running test can be stopped at any time with **Cancel** (the rows
already measured stay in the table).

**Feasible = "at most (1 − min success %) of the requests may fail"** (90% by default). When a level
fails, the climb along that axis stops. The duration is bounded by early stopping and the request
timeout — no full 128×256k cross-product is ever run.

> ⚠️ The optimum finder is a **load test**: run it only against servers you **own or are authorised
> to test**.

**Settle pause:** every measurement is preceded by a pause (**3 s** by default) so the server can shed
the previous burst — free the KV cache, drain the queue, let the rate-limit window recover — before
the next concurrency / size / profile is tested. Without it, **one level's residual load spills into
the next** (e.g. misleading "at capacity" 429s that are really just stacked-up requests). Raise it for
a shared/rate-limited gateway; set it to 0 for a dedicated local server.

Configurable: **concurrency levels** (default `1,2,4,8,16,24,32,48,64`), the request-size list
(default 1024–65536), the generation-length list, the workload profiles, the concurrency-phase
context, gen tokens per request, requests per worker, the context cap, min success %, the **settle
pause**, and whether to run the request-size / generation-length / profile sweeps.

## Soak test

The **Soak** tab measures **sustained throughput — how many tokens in and out the server (with its
backends) really delivers per hour** under continuous load. Unlike the other tests (which send a fixed
number of requests and finish), the soak test **holds a fixed concurrency for a set time** (e.g.
30 min) and keeps sending requests.

- `concurrency` workers send requests back to back, so **exactly `concurrency` requests are in flight
  at all times.**
- Shown live: **IN / OUT / TOTAL tok/s** and their **tokens/hour** projection (`tok/s × 3600`), req/s,
  TPOT, latency, errors — plus an **output tok/s per minute chart** (does throughput **stay stable**
  or fall off: thermal throttling, memory, gateway failures).
- Output length is forced with `ignore_eos`; **raise Timeout for large outputs.**
- A run can be interrupted with **Stop** (the last numbers stay on screen).

**Production workload (optional):** instead of a fixed request size it **replays a real
production traffic mix** — every request picks a task (weighted by that task's share of calls:
classify, understand, extract, profile update and so on) and samples the input/output token counts
from that task's measured distribution (a lognormal fit to the mean/p95). That gives you a
**realistic sustained tokens/hour** figure for a mixed workload (mostly short structured calls,
~1.3k in / ~150 out, plus the occasional heavy generation job). The input/output fields are
ignored — you only change **the duration and the concurrency**.

The bundled mix is `PRODUCTION_TASKS` in `benchmark.py`, measured from a live analysis pipeline.
**Replace that table with your own measurements** to soak-test against your traffic rather than the
sample.

**Overload probe (+10%, on by default):** runs **10% above the concurrency limit** (e.g. 64 → 72) to
check **admission control** — does the server reject the surplus requests properly (like OpenRouter /
HuggingFace / a well-configured vLLM), or does it accept everything and degrade silently? The verdict
says which happened:
- **✅ 429/503** on the surplus → correct backpressure;
- **⚠ no rejection, but the output is truncated** → no admission control;
- **❌ timeouts / hard errors** → the server breaks under overload.
Rejections (429/503) are counted separately from "hard" errors (timeout, connection, 500) in the table.

**Example:** concurrency 64, input 4000 / output 500 tokens (RAG-like), 30 min → you see something
like "IN 136 M/h · OUT 7.6 M/h" and whether it held for the full 30 minutes.

> **Choose the concurrency wisely:** run the **Optimum finder** first and use its peak/knee value —
> then the soak test measures the *maximum* sustained throughput. And remember that tokens/hour
> depends on the workload's shape (the input/output ratio): a RAG load delivers a lot of tokens **in**,
> chat/agentic more **out**.

## Capacity (tok/min ceiling)

The **Capacity** tab answers one question: **how many tokens per minute can this endpoint really
do?** That is the capacity **ceiling** — the number you put in an SLA or a capacity-planning document.

How it differs from the other tabs:

| Tab | What it does |
|-----|--------------|
| **Optimum finder** | a quick sweep → the best concurrency, an instantaneous peak tok/s |
| **Soak** | a *fixed* concurrency held for 30 min → tokens per **hour** (endurance) |
| **Capacity** | *ramps* concurrency in steps → peak sustained tokens per **minute** (the ceiling) |

How it works:

- **Ramp:** concurrency steps **1 → 2 → 4 → 8 → … → Max concurrency**. At each step the test keeps
  that many requests continuously in flight for **"Window / step"** seconds (40 s by default). Choose
  a window **longer than a request takes** (see the latency p95 on the Benchmark tab) — if no request
  finishes inside the window, the test says so explicitly: *"raise Window / step"*.
- **Steady-state measurement:** the first ~third of each window is discarded (the queue filling up, a
  cold KV cache); **IN / OUT / TOTAL tokens per minute** are measured over the rest.
- **Chart:** a saturation curve per level — **red dots** are beyond capacity (saturation), a **green
  ring** marks the measured peak. The large readout is coloured by the result (green = capacity found
  / target met, red = not).
- **Saturation detection** — the ramp stops early when:
  - throughput **plateaus** (adding concurrency no longer raises tok/min, < 8% growth), **or**
  - the server starts **rejecting** (429/503 — the admission limit is reached), **or**
  - **hard errors/timeouts** appear or **the output is truncated** (decode is saturated).
- **Result:** the **peak sustained TOTAL tok/min** (= the capacity), **at which concurrency** it was
  reached, and **why** the ramp stopped. If the ramp reaches max concurrency without stopping it says
  *"still climbing — raise Max concurrency"* (the real ceiling is higher).

**Target tok/min (optional):** fill in the capacity you need (e.g. a contractual TPM, or the peak load
you must serve) and the result gains a **PASS/FAIL** verdict — does the measured peak capacity meet it?
Leave it empty and it simply measures the ceiling.

**Example:** Max concurrency 64, input 1000 / output 500, 40 s/step → the ramp runs 1→2→4→8→16→32→64
and you see e.g. "CAPACITY 31.2 M/min @ c=8 · saturation: the server started rejecting with 429 at
c=32". If you set Target 5 M/min → **✅ PASS**.

> ⚠️ Like the Optimum finder and Soak, Capacity is a **load test** — run it only against servers you
> own or are authorised to test.

## Model fit (agentic suitability)

The separate **Model fit** tab does not measure speed but **capability**: is the model suitable for
agentic use? It runs a battery of a couple of dozen short probes (deterministic, temperature 0) and
gives a verdict of **FIT / BORDERLINE / NOT FIT**. The **"Disable thinking during test"** checkbox
(on by default) tests a Qwen3-style reasoning model in its agentic mode.

The dimensions tested (each one toggleable, each scored 0–100%):

1. **Tool calls** — the model is given tools through the **native OpenAI `tools` API** (get_weather,
   web_search, calculator, send_email — what routers/vLLM/TGI/SGLang actually use); a model that only
   knows the prompt convention still gets credit if it emits a Hermes `<tool_call>` block in the text.
   Scored on: does it call a tool, does it pick the **right tool**, does it fill in the **right
   arguments**, and — importantly — does it **not** call a tool when the question needs a plain answer
   (the false-call rate should be 0%). This is the core agentic capability.
2. **Structured JSON output** — the model is asked for a specific JSON shape with no prose or code
   fences; scored on whether the answer parses and matches the required keys/types (the thing that
   otherwise breaks a `json.loads()` pipeline).
3. **Instruction following & format discipline** — strict format instructions (exactly one word, a
   number only, three lines) plus a check that the model **does not leak thinking / `<think>`
   scaffolding** into the visible answer an agent has to parse.
4. **Latency & throughput** — measures response latency and output tok/s across all the probes.

**The verdict** is a weighted blend (tool 0.5, JSON 0.25, instructions 0.25) plus a **hard gate**: if
the model cannot reliably call tools (valid tool-call rate < 50%) the result is always **NOT FIT**, no
matter how clean the rest is. The results table shows every probe separately (✓/✗ + detail) so you can
see exactly where the model stumbles.

## Provider fit (OpenRouter / HuggingFace)

The separate **Provider fit** tab answers two questions: **would this backend survive real OpenRouter
/ HuggingFace inference traffic**, and **where does it break first**? Two phases:

### 1. API-contract compliance

Quick single probes, each answering one hard requirement a router places on a server (✓/✗):

- **Chat endpoint** — `/v1/chat/completions` returns a response (OpenAI compatibility).
- **Streaming (SSE)** — the response arrives token by token (TTFT < total time), not buffered.
  OpenRouter: *"stream tokens immediately rather than queueing"*.
- **Usage accounting** — the server returns prompt/completion token counts. Needed for accurate
  token-based accounting/throughput (a router-permitted parameter), though not strictly a provider
  requirement.
- **max_tokens** — generation stops at the limit; **finish_reason=length** on truncation.
- **Stop sequences** — the `stop` parameter is honoured (an OpenRouter-permitted parameter).
- **Determinism (temp 0)** — the same prompt gives identical output (greedy decoding).
- **Sampling parameters** — temperature/top_p/seed **actually apply** (a different seed → different output).
- **Concurrent correctness** — a concurrent burst of requests succeeds in full.
- **Clean error codes** — a malformed request gets a 4xx JSON error, not a 5xx / a hang (OpenRouter's
  uptime rules: a 400 does not count against uptime, a 500+ does).
- **Auth enforcement** — a request with a deliberately wrong API key gets 401/403. An open endpoint (a
  wrong key accepted) is fine in local development but not for a live provider → a non-critical gate.
- **Tool calling (native API)** — the real OpenAI `tools`/`tool_choice` API parameter (what
  OpenRouter/vLLM/TGI/SGLang actually use); it reads `tool_calls` off the response (streaming and
  non-streaming). **Gates both the OpenRouter and the HuggingFace verdict.** (n/a on the
  `/v1/completions` endpoint — the legacy API has no tools.)
- **Tool calling (Hermes prompt)** — a **fallback check** that runs ONLY when the native API (above)
  does not work. A model with native tool calling does not need the prompt-based
  Hermes/NousResearch `<tool_call>` XML convention, so in that case it simply shows _"n/a — native
  tool-calling works"_ (green) rather than a confusing red. If the native path fails, the Hermes
  convention (Openclaw/agent frameworks) is tested and on failure the model's actual answer is shown.
  **It does not affect the verdict** either way — it is informational.
- **Structured output** — the requested JSON shape parses and matches the schema. **HF tests
  structured output.**
- **/v1/models metadata** — `context_length` (+ pricing) is published. Both routers read these from
  `/v1/models` (OpenRouter's model spec; HF's `:fastest`/`:cheapest` selection).

### 2. Integrity tests — *"is an unaudited third party cheating us or our users?"*

Adversarial probes a router would run against a backend it does not operate itself:

- **Token-count honesty** — forces a known output length (`ignore_eos`) and compares the server's
  reported `completion_tokens` against a tokenizer-agnostic estimate derived from the text. A high
  ratio = **billing inflation**. This is a **hard block** for OpenRouter (the router bills by tokens →
  over-counting cheats users directly).
- **Context honesty** — hides a code in a long prompt (needle-in-a-haystack) at several depths near
  the server's advertised limit and asks for it back. It fails if the server **silently truncates** or
  the promised context is not real.
- **Model quality / authenticity** — a golden-answer eval (facts/maths/logic). A silently
  **quantised / wrong / broken** model fails these. *Not a definitive quantisation detector, but the
  first quality floor a router would apply before trusting a backend.*
- **Client-cancellation handling** — measures a probe TTFT, floods the server with several long
  requests that **disconnect after the first token** (as a router does when a user cancels), then
  measures the probe TTFT again. If the server freed the slots → fast; if it kept generating for the
  abandoned requests → the probe waits in the queue. *Informational (timing-sensitive), but a big jump
  is a genuine warning sign.*
- **Logprob fingerprint** — the model's confidence on a trivial fact (a proxy for accuracy;
  informational, many servers do not expose logprobs).

> **🧠 Reasoning models** (DeepSeek-R1 / Qwen thinking / QwQ and similar): if a model puts the visible
> answer behind hidden reasoning and a small token budget is spent entirely on that, `content` would be
> empty and the tests would fail for the wrong reason. The tool **detects a reasoning model in both
> common forms** — as a separate `reasoning_content`/`reasoning` field (e.g. hosted routers) **and** as
> `<think>` tags directly inside `content` (common on local servers — vLLM / llama.cpp / SGLang),
> including when the token budget never reaches the closing `</think>` tag (unfinished reasoning is
> counted wholly as reasoning, not as the answer). It **counts reasoning tokens towards token honesty**
> (a thinking model is not accused of inflation) and gives the correctness/quality probes an
> **extended budget** while stripping `<think>` to reach the visible answer. The report is marked
> "🧠 reasoning model".

### 3. Concurrency sweep — finding the bottleneck

Walks the concurrency levels (e.g. 1,4,8,16,32) with a realistic request shape and measures, at each
level, output tok/s, TTFT **p95 and p99** (tail latency), end-to-end latency p99, TPOT (time per
output token), req/s and **429/503 vs hard errors**. From that it derives:

- **The throughput knee** — the concurrency at which tok/s stops growing (the sustainable ceiling).
- **The first bottleneck** — the dominant signal:
  - **Prefill / queue bound** — TTFT p95 explodes under load while TPOT stays flat → requests are
    waiting in the queue (the scheduler/prefill is the constraint); throughput is fine but
    time-to-first-token suffers.
  - **Decode bound** — TPOT rises under load → KV-cache / memory-bandwidth pressure in the decode batch.
  - **No batching** — tok/s does not scale with concurrency (a single request already saturates the
    GPU); bad for TGI-style throughput.
  - **Breaks under load** — hard errors / timeouts instead of clean rejections.

**Admission control** is scored as its own dimension from an overload probe (+25%): a clean rejection
(429/503) vs breaking vs silently swallowing. OpenRouter asks verbatim for *"return early 429s if under
load, rather than queueing requests"*.

**A verdict per provider** (the emphases differ):
- **OpenRouter** — streaming, usage, **token-count honesty** (a hard block), stop/max_tokens, auth
  enforcement, `/v1/models` metadata, **context honesty + model quality**, clean errors, **TTFT p95 ≤
  the SLA and p99 ≤ 2× the SLA** at the knee, stability under load.
- **HuggingFace / TGI** — streaming, concurrency, `/v1/models` metadata, **TTFT < 5 s** (HF's
  documented threshold, single-call streaming), **tool calling + structured output** (HF validates
  both), **context honesty + model quality**, and throughput — **batching must scale (≥1.5×)**.

The result is stored in History (peak tok/s) so you can see the change between runs. The
**"Copy sweep table"** and **"Copy report"** buttons copy, respectively, the concurrency sweep table
(tab-separated) or the whole test transcript (compliance + integrity + verdicts) to the clipboard —
handy for sharing a result.

> **🧠 "Disable thinking during test" (on by default):** sends
> `chat_template_kwargs.enable_thinking=false` with every request, testing a Qwen3-style reasoning
> model in its agentic mode. In thinking mode such a model tends to "overthink" and answer in prose
> without calling a tool — the tool probes would then fail even though the model is capable. Uncheck
> it to test the thinking variant as-is. Servers that do not support the parameter ignore it.

> **A note on sources:** the compliance checks follow OpenRouter's
> ([provider integration](https://openrouter.ai/docs/guides/community/for-providers)) and HF's
> ([register-as-a-provider](https://huggingface.co/docs/inference-providers/en/register-as-a-provider))
> documentation. **The bottleneck taxonomy (prefill/decode/queue/batching), however, is this tool's own
> analytical framework** — justified by how vLLM/TGI actually work, not a direct requirement from the
> routers' documents (OpenRouter publishes only TTFT and throughput).

> **What an inference API honestly CANNOT check:** *no-charge-on-error* (whether a failed request goes
> unbilled) requires the router's billing API, not just the inference endpoint; and a **real
> quantisation fingerprint** would need reference logprobs per model (we only have a confidence proxy).
> Those are deliberately left uncovered.

## Capabilities (what the endpoint offers)

The **Capabilities** tab **maps what this server/model actually offers** — useful when you need to
know quickly whether an endpoint supports e.g. **embeddings**, rerank or vision, without digging
through documentation. Each row is **one small probe** against the current Host / Model, with a result
of ✓ *supported* / ✗ *no* / ~ *present* (the route exists but does not work for this model) / — *n/a*.

Three groups:

- **API routes** — does the server serve these endpoints:
  - `/v1/models` (the model list), `/v1/chat/completions`, `/v1/completions`
  - **`/v1/embeddings`** — when it works, the **vector dimension** is shown. If the selected (chat)
    model does not embed, **other models under `/v1/models` are tried automatically**
    (embedding-sounding names first) — so one scan tells you whether the router offers embeddings at
    all, and **with which model**.
  - `/v1/rerank` (or `/rerank`), `/tokenize` (vLLM), `/v1/moderations`
  - `/v1/images/generations`, `/v1/audio/speech` (TTS), `/v1/audio/transcriptions` (STT)
  - *Route detection:* any response other than 404 (including a 400/422 validation error) means the
    endpoint exists — that distinguishes "no such endpoint" from a real "the endpoint is there, but
    this model does not fit it".
- **Chat features** (when the chat endpoint works):
  - **Streaming (SSE)**, **native tool calling**, **JSON object mode** and **JSON schema mode**
    (structured outputs), **vision** (image input), **multiple responses (n>1)**, **logprobs**,
    **stop sequences**, **reproducible sampling (seed)**, **reasoning/thinking** output.
- **Model metadata** — from the `/v1/models` entry: context length, pricing, owner, model count.

The scan makes about two dozen quick requests and **puts no load on the server**. The result can be
copied to the clipboard as a tab-separated table with the **Copy results** button.

## Embed speed (embedding throughput)

The **Embed speed** tab measures an **embedding model's performance and speed** — how many vectors per
second the server can produce and how fast a single request answers. It differs from the other tests,
which measure a chat model's generation: here the load goes to the `/v1/embeddings` endpoint.

How it works:

- **Batch load:** `concurrency` workers send requests continuously, each request carrying
  **`batch_size` texts** (~`input_tokens` tokens per text) for `duration_s` seconds. Embedding servers
  **batch very efficiently**, so batch size is its own knob — a larger batch usually means many more
  embeddings/s (up to the server's limit).
- **Preflight:** before the test one small embed is made to confirm the model **really embeds** and to
  get the vector dimension. If you pick a chat model that does not embed, the test stops with a
  **clear message** (e.g. *"model X is not an embedding model"*).
- **Model choice:** the embedding model is **usually different** from your chat model (e.g. bge-m3,
  e5, nomic). Enter it in the "Embedding model" field (leave it empty to use the model selected
  above). Run the **Capabilities** tab first to see which model embeds.

**The result:** **embeddings/s** (vectors per second), **input tokens/s**, req/s, the **vector
dimension**, latency **p50/p95** and **ms per embedding**. A live chart shows embeddings/s over time.

**Example:** bge-m3, batch 32, concurrency 8, 64 tok/text, 15 s → you see e.g. "2 500 emb/s ·
160 K tok/s (dim 1024) · p95 24 ms". Raise the batch size (64 / 128) to find the peak throughput.

> ⚠️ Like the other load tests, **Embed speed puts load on the server** — run it only against servers
> you own or are authorised to test.

## Embed quality (do the embeddings work)

The **Embed quality** tab answers what speed cannot tell you: **are these embeddings actually any
good?** A fast model is useless if the vectors are meaningless. Each row is one small probe → ✓ *pass*
/ ✗ *fail* / ~ *weak*, with the measured numbers. Four groups:

- **Retrieval & similarity** — do the embeddings carry meaning:
  - **Retrieval ranking:** a query + documents → **the right document must get the highest cosine similarity**.
  - **Paraphrase vs unrelated:** the similarity between paraphrases must be **clearly higher** than for unrelated texts.
  - **Multilingual (Estonian↔English):** an Estonian sentence must embed **closer to its English
    translation** than to an unrelated English sentence — this tests cross-lingual retrieval (relevant
    for Estonian-language content).
- **Vector properties** — is it suitable for a vector database:
  - **L2 normalisation** (‖v‖ ≈ 1 — many vector DBs assume it), **determinism** (the same text → an
    identical vector), **dimension**.
- **Limits** — for designing client-side batching:
  - **Max input length** (where the model truncates or errors) and **max batch size** (texts per request).
- **Rerank** (if `/v1/rerank` exists) — **relevance:** does the reranker put the relevant document
  first? If the embedding model is not a reranker, a rerank-sounding model is looked up automatically.

The scan makes about 20 quick requests and **puts no load on the server**. The embedding model is
usually different from the chat model — run the **Capabilities** tab first to see which model embeds.
**Copy results** copies the table tab-separated.

## Vision (VL) — does the model understand images

The **Vision** tab checks whether a **vision-language (VL) model really understands images** — not
merely whether the server accepts an image (that is what Capabilities does). The tester **generates
images with known content itself** (pure Python, no extra dependency) and compares the model's answer
against the truth. Ideal for testing 8B VL models (Qwen2.5-VL, Llama-3.2-Vision, InternVL, Pixtral,
LLaVA).

The checks:

- **Accepts an image** — does the model take an image at all (a text-only model returns 400 here and
  the rest are skipped with a clear "not a vision model" message).
- **Colour recognition** — three single-colour images (red/green/blue); the model must name the colour (≥2/3).
- **Reading text (OCR)** — a word in a block font ("CAT"); the model must read it.
- **Reading a number (OCR)** — a number in a block font ("42").
- **Counting** — a row of squares; the model must count them.
- **Multiple images** — two images in one request; the model must name both colours (multi-image support).
- **Latency** — the mean latency of an image request (VL is slower).

Every row shows **✓ pass / ✗ fail / ~ partial** and **the model's actual answer**, so a failure is
self-diagnosing. A VL model is usually a separate model — enter its name in the "Vision model" field
(leave it empty to use the model selected above). **Copy results** copies the table.

## Presets, comparison and reports

### Load presets

The **Connection** tab has three buttons that fill sensible parameters into the Benchmark, Soak and
Provider-fit tabs **at once**, so you do not have to set every field by hand:

| Preset | Description | Example (in / out / concurrency) |
|---|---|---|
| **Chat** | Short prompts, short answers, moderate concurrency (interactive chat) | ~1k / 256 / 8–32 |
| **RAG (long context)** | Large input, moderate output | ~8k / 256–500 / 8–16 |
| **Agent / batch** | High concurrency, short structured output | ~2k / 384 / 32–64 |

The values are a starting point — adjust them by hand afterwards if you need to.

### Cross host/model comparison

On the **History** tab select **several rows** (Cmd/Shift-click) and press **"Compare selected"** — a
side-by-side table opens: **rows = metrics, columns = runs** (labelled `host:port · model · test`).
Unlike the ordinary between-runs comparison (which groups by identical config), this works **across
different servers, models and configurations** — a real "server A vs server B" or "model X vs model Y".

### Shareable report

**"Export report"** saves the selected run(s) as a **Markdown** or **HTML** file (chosen by the
extension). A single run gives metadata + a metrics table; several selected give a comparison table.
The same button is in the comparison window too. All results can also be exported as **CSV**
("Export CSV…").

### Convenience

- **Completion notifications** — when a long test (≥ 8 s) finishes you get a macOS desktop
  notification plus a sound (so you can walk away while it runs).
- **Keyboard shortcuts** — `Cmd+R` runs the active tab's test, `Cmd+.` / `Esc` stops, `Cmd+D` detects
  the server, `Cmd+L` lists the models.

## How it works

- **Speed measurement** uses streaming (`stream=True`): the time to first token (TTFT) is measured
  when the first chunk with content arrives, and the decode rate = tokens / (end − first token).
  If the server supports `stream_options.include_usage`, exact token counts are used (vLLM, SGLang).
- **Detection** tries several fingerprints: `/v1/models`, `/api/tags` (Ollama), `/props` (llama.cpp),
  `/get_model_info` (SGLang), `/info` (TGI), `/version` (vLLM) plus a port heuristic. "Detect server"
  first works through the scheme candidates (https/http) until one answers.
- **The optimum finder** builds on the load test (`load`): phase A binary-searches the max context (or
  reads the server's reported limit), phase B climbs concurrency with early stopping and computes the
  peak/knee, phase C finds the highest working concurrency at each context size. Every request gets a
  unique filler prompt so prefix caching cannot distort the numbers.
- **The scan** runs an asynchronous TCP-connect scan across the subnet on the selected ports and then
  fingerprints the servers behind the open ports it found.

## Ethical note

Scan only networks you **own or are authorised to test**. Scanning networks that are not
yours may be illegal.

## Project structure

```
llmscanner/
├── gui.py        # Tkinter GUI (the main one) — Connection / Benchmark / Optimum finder / Soak / Capacity / Model fit / Provider fit / Capabilities / Embed speed / Embed quality / Vision / Scan / History
├── cli.py        # command-line interface (bonus, needs rich)
├── client.py     # OpenAI-compatible async client (http/https, base_path) + timing
├── detect.py     # server detection / fingerprinting + smart_detect (trying candidates)
├── scanner.py    # network scan + port detection
├── testimg.py    # dependency-free PNG generator (colours/text/squares) for the Vision test
├── benchmark.py  # latency / load / context / sanity / sweep + find_optima + soak_test + capacity_test + suitability_test (model fit) + provider_readiness + capabilities_probe + embed_speed_test + embed_quality_test + vision_test
├── store.py      # SQLite persistence: saved hosts + all results
├── icon.py       # generates the app icon (a blue V)
├── assets/
│   └── icon.png  # the generated window icon
├── models.py     # data classes (ServerInfo, RequestResult)
└── util.py       # helpers + Target / resolve_target (parsing the smart host field)

app_entry.py      # PyInstaller entry point (launches the GUI)
build_macos.sh    # builds the standalone one-file macOS executable (dist/LLMScanner)
run.sh            # runs the app (creates the venv + installs on first run)
run.command       # double-clickable macOS Finder launcher
CHANGELOG.md      # change log
```

GUI internals: the reusable components `ChartCanvas` (a lightweight line chart) and `LiveLog`
(a streaming coloured log) are shared between tabs; the tables are `ttk.Treeview`, styled to match
the CustomTkinter appearance (zebra striping, reorderable columns).

### Help, theme and language

At the **top right** of the window there is a settings bar:

- **❔ Help** — opens the guide (a tour of the tabs + tips) and the infrastructure and support contacts.
- **Theme** — System / Light / Dark; applies immediately.
- **Language** — English (primary) / Estonian.

The theme and language choices are **remembered** (stored under `~/.llmscanner`) and restored on the
next launch. Switching language rebuilds the tabs — it is not done while a test is running.

### Where the data lives

Saved hosts, all test results and the settings (theme, language) are kept in
`~/.llmscanner/llmscanner.db` (SQLite). The location can be changed with the `LLMSCANNER_HOME`
environment variable.

A saved host profile stores its **API key in the clear**, so the directory is created `0700` and the
database file `0600` (owner only) — an existing directory left more permissive is tightened on the
next run.

The change history is in [CHANGELOG.md](CHANGELOG.md).
