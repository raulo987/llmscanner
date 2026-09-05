# Changelog

All notable changes to this project. The format follows the
[Keep a Changelog](https://keepachangelog.com/) principles.
Current version: **0.1.0** (no releases tagged yet; entries are by date below).

## [Unreleased]

### 2026-09-06 (English throughout; TLS, key storage and port-parsing fixes)
- **The docs and the last Estonian strings are now English.** README.md and CHANGELOG.md are fully
  translated (internal anchors updated and verified). The verdict strings returned by
  `_suitability_verdict` / `_rd_verdict` were Estonian **regardless of the language setting** — they
  are rendered directly, not through `tr()`, so an English UI showed SOBIB / PIIRIPEAL / EI SOBI for
  Model fit and Provider fit. They are now **FIT / BORDERLINE / NOT FIT**. The app's Estonian locale
  (`LANG` / `TR_ET` / `_help_text`) is untouched, as is `_ML_ET` — the Estonian half of the
  cross-lingual embedding probe, whose whole point is to be in another language.
- **TLS verification is no longer disabled everywhere.** `verify=False` was unconditional, which was
  right for a local server with a self-signed certificate and wrong for a public host: every request
  carries the API key in an `Authorization` header, so an unverified connection exposed that key to
  interception. Verification is now off only for a private/loopback host (`util.tls_verify_for`) and
  on for anything public; `LLMSCANNER_INSECURE_TLS=1` overrides it.
- **Saved API keys are no longer world-readable.** `~/.llmscanner` was created `0755` and its SQLite
  file `0644`, while a saved host profile keeps its API key in the clear — any other user on the
  machine could read them. The directory is now `0700` and the database `0600`, and an existing
  directory left more permissive is tightened on the next run.
- **`parse_ports` validates and bounds its input.** It accepted any integer and any range, so a typo
  such as `1-5000000` built a five-million entry list and then tried to open that many sockets. Ports
  are now checked against 1..65535, a backwards range is rejected, and the total is capped at 4096.
  `llmscanner-cli scan` catches the resulting error and exits 2 instead of printing a traceback, and
  `main()` now propagates command exit codes.
- **The soak workload mix is no longer tied to one customer.** The task table is now
  `PRODUCTION_TASKS` (`benchmark.py`) with generic task names and each task's *share* of calls rather
  than absolute call counts, sampled by `workload_sample()`; the checkbox reads "Production workload".
  The distributions — and so the sampling behaviour — are unchanged, and the docs now point at the
  table for swapping in your own measurements.

### 2026-07-07 (new Vision tab — image understanding in a VL model)
- **New "Vision" tab (after Embed quality)** — checks whether a **vision-language (VL) model really
  understands images**, not merely whether the server accepts one. The tester **generates images with
  known content itself** (a new `testimg.py` — a dependency-free PNG encoder + a 5×7 block font, no
  Pillow) and compares the answer against the truth:
  - **Accepts an image** (a gate — a text-only model fails here and the rest are n/a),
    **colour recognition** (3 colours, ≥2/3), **text OCR** ("CAT"), **number OCR** ("42"),
    **counting** (a row of squares), **multiple images** (two at once) + **latency**.
  - Every row shows the model's actual answer, so a failure is self-diagnosing.
- The client gained a `chat_image(model, prompt, image_urls)` method (a multimodal chat request).
  Backend: `vision_test()`. Suits 8B VL models (Qwen2.5-VL, Llama-3.2-Vision, InternVL, Pixtral, LLaVA).

### 2026-07-07 (new Embed quality tab — do the embeddings work)
- **New "Embed quality" tab (after Embed speed)** — checks the **quality of the embeddings, not their
  speed**. Four groups of pass/fail rows plus the measured numbers:
  - **Retrieval & similarity:** retrieval ranking (the right document gets the highest cosine
    similarity), paraphrase-vs-unrelated similarity, **Estonian↔English cross-lingual** alignment.
  - **Vector properties:** L2 normalisation (‖v‖≈1), determinism (the same text → an identical
    vector), dimension.
  - **Limits:** max input length (the truncation/error point) and max batch size.
  - **Rerank** (when `/v1/rerank` exists): relevance — does the reranker put the right document first;
    if the embedding model is not a reranker, a rerank-sounding model is looked up automatically.
- A preflight embed plus a clear error message when a chat model is picked. Copy results copies the
  table tab-separated.
- Client: `embed(..., keep_vectors=True)` also returns the vectors; backend `embed_quality_test()` +
  cosine/rerank helpers.

### 2026-07-07 (new Embed speed tab — embedding model performance)
- **New "Embed speed" tab (after Capabilities)** — measures an embedding model's **throughput and
  speed**: it holds `concurrency` batch requests (each with `batch_size` texts of ~`input_tokens`
  tokens) against the `/v1/embeddings` endpoint for `duration_s` seconds and reports **embeddings/s,
  input tokens/s, req/s, the vector dimension** and latency **p50/p95** + ms/embedding. Live chart
  (emb/s over time).
- **Batch size is its own knob** (embedding servers batch efficiently — a bigger batch = more emb/s).
  A **preflight** embed confirms before the test that the model really embeds; picking a chat model
  stops the test with a clear message. An "Embedding model" field (empty → uses the model selected
  above), since the embedding model is usually different from the chat model.
- The client gained a timed `embed(model, inputs)` method (dim + usage tokens); backend
  `embed_speed_test()`.

### 2026-07-07 (Capabilities: more accurate JSON-mode detection + cleanup)
- **JSON schema detection no longer returns a false "yes"** — a server that silently ignores
  `response_format` (returning prose) used to show "yes" whenever the output happened to be JSON. It
  now uses a natural prompt and **checks that the output matches the required schema** (city +
  population); only then "yes" ("enforced the schema"), otherwise "no". The chat-feature probes now
  send `stream:false` (a streaming server no longer breaks the JSON parse). Removed a redundant branch
  in `_json_probe_row`.

### 2026-07-07 (Capabilities: embeddings tries every router model)
- **The embeddings probe now tries several models** — if the selected (chat) model does not embed, the
  scan automatically walks the other models under `/v1/models` (embedding-sounding ones like
  bge/e5/gte/nomic first) and reports **whether any of them embeds and with which model** (up to 12
  models). With a multi-model router that answers "do embeddings work at all" in a single scan. The
  result distinguishes: ✓ *via model X (dim N)* / ✗ *route present, no embedding model found* /
  ✗ *no route*.

### 2026-07-07 (Capabilities: fixed a timeout crash + a /health probe)
- **Fixed `TypeError: httpx.AsyncClient() got multiple values for keyword argument 'timeout'`**, which
  crashed EVERY endpoint probe on the Capabilities tab (chat, completions, embeddings and so on all
  falsely showed "no" with a TypeError detail). The cause: `probe_json` passed `_http(timeout=…)` while
  `_http` already set `timeout` itself — `_http` now uses `setdefault`, so the caller can override it.
- **Added a `/health` route probe** (most routers/vLLM offer it). Now tested with a real `LLMClient`
  plus an httpx MockTransport (not just a mock object), which would have caught this bug immediately.

### 2026-07-07 (new Capabilities tab — feature discovery)
- **New "Capabilities" tab (after Provider fit)** — maps what an endpoint/model offers. Three groups:
  **API routes** (`/v1/models`, chat, completions, **`/v1/embeddings`** with the vector dimension,
  `/v1/rerank`, `/tokenize`, `/v1/moderations`, images, audio speech/transcribe), **chat features**
  (streaming, native tool calling, JSON object/schema mode, vision, n>1, logprobs, stop sequences,
  seed reproducibility, reasoning) and **model metadata** (context length, pricing, owner). Each row =
  one small probe → ✓ supported / ✗ no / ~ present / — n/a.
- Route detection: any response other than 404 counts the route as existing, which distinguishes "no
  such endpoint" from a real "the endpoint is there, but this model does not support it" (e.g.
  embeddings on a general model).
- The results are shown in a colour-coded tree (group headers + green/red/orange rows); **Copy
  results** copies the table tab-separated. The scan puts no load on the server (~two dozen quick
  requests).
- The client gained a low-level endpoint prober, `probe_json(method, path, body)`; backend
  `capabilities_probe()`.

### 2026-07-06 (Provider-fit tab layout fixed)
- **The Provider-fit field layout is fixed** — the right-hand pair of fields (Output tokens / Requests
  per level / Context probe) used to be thrown far to the right edge (partly off-screen). The cause: an
  unwrapped intro text inflated the grid to ~1650px wide and, without a stretching column, the fields
  scattered. Now: the intro **wraps** to the pane width (and dynamically on resize), the fields sit in
  **two aligned columns** (labels right-aligned, even spacing) and a **stretching tail column** keeps
  the fields compact on the left. The four check boxes are gathered under a "Checks" sub-heading.
- **New "Capacity" tab (between Soak and Model fit)** — measures the endpoint's **peak sustained
  tokens/minute**, i.e. the capacity **ceiling**. Unlike Soak (fixed concurrency → tokens/hour),
  **Capacity ramps concurrency in steps (1 → 2 → 4 → … → Max concurrency)**, holds the load at each
  step for "Window / step" seconds (40 s by default), discards the first ~third of the window (warm-up)
  and measures steady-state IN/OUT/TOTAL tok/min over the rest.
- **Auto-saturation:** the ramp stops early when throughput plateaus (< 8% growth), the server starts
  rejecting (429/503), hard errors/timeouts appear or the output is truncated. The result shows the
  **peak TOTAL tok/min, at which concurrency** and **why the ramp stopped** (+ a tok/h projection and a
  saturation-curve chart). If it reaches the max without stopping → *"still climbing"*.
- **An optional "Target tok/min" field → a PASS/FAIL verdict:** does the measured peak capacity meet
  the required tokens/minute (e.g. a contractual TPM)? Leave it empty and it simply measures the ceiling.
- Backend: `capacity_test()` + `_capacity_levels()` (benchmark.py); GUI: `_build_capacity_tab` +
  handlers, Estonian translations, Cmd+R support. Like the Optimum finder / Soak, Capacity is a load test.

### 2026-07-06 (Capacity tab visual polish + sharper diagnostics)
- **A more readable chart:** the axis labels are now compact (31.2M, not 31242857) — the fix applies to
  every chart (Soak, Benchmark and the rest). On the Capacity curve the **saturation points are red**
  and the **peak is ringed** with a green "peak" marker.
- **A coloured verdict:** the large readout turns green with the result (capacity found / target met)
  or red (target missed / no sustained capacity); the target result (✅/❌) is now in the readout too.
  The log's step lines are aligned into columns, with a ⚠ at unhealthy levels.
- **Sharper diagnostics:** when no request finishes inside the measurement window (the window is
  shorter than a request takes), it now says clearly *"no request finished inside the measurement
  window (a request takes ~Xs vs Ys window) — raise ‘Window / step’"* instead of the previous
  misleading "output truncated".
- The backend now emits the `step_done` snapshot with a fresh peak (previously the readout lagged one
  step behind).

### 2026-07-06 (the Hermes tool probe is now a fallback, not always run)
- **Provider fit's "Tool calling (Hermes prompt)" check is now a FALLBACK** — it only runs when the
  native `tools` API check (which gates the verdict) does not work. A model with native tool calling
  (e.g. Qwen3) does not need the prompt-based Hermes/NousResearch `<tool_call>` XML convention, so it
  now shows a green _"n/a — native tool-calling works"_ instead of a confusing red _"0/3 correct Hermes
  tool calls"_. If the native path fails, Hermes is tested as before (3 cases, showing the model's
  actual answer). Neither check affects the verdict — both are informational.

### 2026-07-06 (a Model-fit "Copy results" button)
- **A "Copy results" button on the Model-fit tab** — copies the report (verdict + scores) and the whole
  probe table with **full detail** (including complete error messages) to the clipboard, tab-separated.
  Same pattern as Provider fit / Benchmark / Optimum finder.

### 2026-07-06 (automatic retry of a transient server error (5xx) on probes)
- **The capability probes (compliance / integrity / model-fit / recall) now retry automatically** on a
  transient 5xx server error (e.g. a momentary 503 overload) or a connection/timeout error (up to 2×
  with a small backoff). Previously a momentary server hiccup could fail the whole test (e.g. every
  tool probe → "HTTP 503" → a false "NOT FIT"). **The load/soak path does NOT retry** — there a 503 is
  precisely the admission-control signal being measured. The client gained a `generate(..., retries=N)`
  parameter and an `_is_transient()` distinction (5xx/connection error retriable, 4xx not).
- **The detail of a failed tool probe now keeps the WHOLE server error message** (previously truncated
  at 60 characters), so double-clicking a Model-fit row shows the complete 503 response — needed to
  diagnose a server-side fault (e.g. a broken tool-generation path). The client now captures up to 600
  characters of the error body.

### 2026-07-06 (calculator cases made explicit; click opens the detail)
- **Double-clicking a row in the Model-fit table opens the model's full prompt and full detail** in a
  separate window (the table columns cut long text off, e.g. "→ no tool call — model said: …").
- **The Model-fit calculator cases now make an explicit tool request** ("Use the calculator tool to
  compute …"). Previously a capable model computed simple arithmetic itself (correctly!) without
  calling the tool, which the test counted as a failure — and that made the tool score wobble randomly
  (e.g. 76% ↔ 88%). The expectation is now unambiguously a tool call, so the case is deterministic.
  (The weather/search/email cases are unchanged — those genuinely need a tool.)

### 2026-07-06 (Model-fit native tool calling; the native gate for OpenRouter too; a completions fix)
- **Model fit now tests native tool calling** (the OpenAI `tools` API), with the Hermes prompt as a
  fallback. Previously Model fit tested **only** the Hermes `<tool_call>` convention, so a model that
  supports native tool calling (but does not emit Hermes XML) falsely got "NOT FIT (Hermes)". A model
  is now credited if it calls a tool **either way**; only a model that does neither scores zero. The
  "(Hermes)" specificity has been removed from the verdict.
- **Model fit gained the "Disable thinking during test" checkbox** (on by default) — the same as
  Provider fit, so a Qwen3-style reasoning model is tested in its agentic mode.
- **Native tool calling now gates the OpenRouter verdict too** (in addition to HuggingFace) — a router
  that sends tool-calling traffic needs the model to support the `tools` API.
- **Fix:** the Provider-fit native tool test now honestly shows "n/a — completions has no tools API" on
  the `/v1/completions` endpoint, instead of a misleading "no tool_calls — model said: …" (the legacy
  completions endpoint cannot take a `tools` parameter at all).

### 2026-07-05 (a disable-thinking option in Provider fit)
- **A new "Disable thinking during test (test the agentic mode)" checkbox** — **on** by default. It
  sends `chat_template_kwargs.enable_thinking=false` with every test request, so a Qwen3-style
  reasoning model is tested in its **agentic (thinking-off) mode**. The reason: Provider fit measures
  whether a backend can serve **agentic / tool-calling traffic**, and in thinking mode such a model
  tends to "overthink" — it reasons in prose and answers directly without calling a tool, so the tool
  probes fail even though the model is capable. Uncheck it to test the thinking variant as-is. Servers
  that do not support the parameter simply ignore it.
- The client (`client.py`) gained a general `extra_body` pass-through — arbitrary top-level request-body
  fields are merged into every request (without threading the parameter through each call by hand).

### 2026-07-05 (a native tool-calling test + diagnostics with the raw answer)
- **A new check: "Tool calling (native API)"** — Provider fit now sends the real OpenAI `tools`/
  `tool_choice` API parameter (not just the prompt-based Hermes convention) and reads the response's
  `tool_calls` field (assembling streaming `delta.tool_calls` fragments as well as non-streaming
  `message.tool_calls`). This is **the actual standard** today, used by OpenRouter/vLLM/TGI/SGLang —
  the old Hermes XML test only tested **one particular fine-tune's convention**, so a good model with
  native tool calling used to get a false "NOT FIT".
  - The old check has been renamed **"Tool calling (Hermes prompt)"** and stays informational (it no
    longer affects the verdict), while **"Tool calling (native API)" now gates the HuggingFace verdict**.
  - The client (`client.py`) gained `tools`/`tool_choice` pass-through and a `RequestResult.tool_calls`
    field; TTFT now also counts tool-call-only responses (otherwise the stream would have looked
    "non-streaming").
- **Failed tool-call probes now show the raw answer.** Previously it simply showed "→ ∅" when nothing
  could be parsed — you could not tell whether the model ignored the tools entirely or tried another
  format. Now (in both Provider fit and Model fit) the model's actual answer is shown (abbreviated), so
  a run is self-diagnosing.

### 2026-07-05 (the window fits the screen; Provider fit copy buttons)
- **The window fits screens of different sizes** — the window's initial size (and the size of the Help
  and Comparison windows) is now computed from the screen's dimensions (up to 92%/88% width/height,
  centred) rather than from a fixed constant. Previously a 1400×1010 window could open **larger than a
  smaller display** (e.g. a smaller laptop screen or a tiled/split-screen layout). The minimum window
  size is bounded by the **computed initial size, not by the screen separately** — a review found that
  two different formulas could compute a `minsize` larger than the initial size on a small screen,
  whereupon Tk would immediately force the window bigger (defeating the fit-to-screen); now
  `minsize ≤ initial size` is always guaranteed.
- **Provider fit — "Copy sweep table" and "Copy report" buttons.** The first copies the concurrency
  sweep table (tab-separated, like Benchmark/Optimum finder). The second copies **the whole test
  transcript** (compliance + integrity + verdicts) to the clipboard as plain text — exactly what you
  need to share a result with a third party (e.g. an engineer assessing the backend).

### 2026-07-05 (reasoning-model support in Provider fit)
- **Reasoning models** (e.g. DeepSeek-R1 / Qwen thinking) no longer produce a false "NOT FIT" report.
  Previously: if a model put the visible answer into `reasoning_content` and a small token budget went
  entirely on hidden reasoning, the tool got the text back empty → a cascade of false failures
  (including a bogus "token inflation ×N" and "quality 0%").
  - The client now captures **`reasoning_content` / `reasoning`** (streaming and non-streaming), folds
    them into the chunks and **measures TTFT from the first reasoning token too** (fixing streaming
    detection).
  - **The token-honesty test counts reasoning tokens** — a thinking model is no longer accused of
    billing inflation (the real tokens are honest even when `content` is empty).
  - Provider fit **detects a reasoning model** and gives the correctness/quality/recall probes an
    **extended token budget** + strips `<think>…</think>` to reach the visible answer.
  - The report is marked "🧠 reasoning model" and History stores `reasoning model: yes/no`.
  - Detection and stripping also work for **local-server-style** reasoning models (vLLM / llama.cpp /
    SGLang), where `<think>` is **directly inside `content`** rather than in a separate
    `reasoning_content` field — including when the token budget never reaches the closing `</think>`
    tag (unfinished reasoning is counted wholly as reasoning, not as the answer).

### 2026-07-05 (convenience & data)
- **Load presets** — **Chat / RAG / Agent** buttons on the Connection tab that fill sensible parameters
  into the Benchmark, Soak and Provider-fit tabs in one click.
- **Cross host/model comparison** — on the History tab select several rows (Cmd/Shift-click) and
  **"Compare selected"** opens a side-by-side table (metrics × runs) — server A vs B, model X vs Y,
  across different configs too.
- **Shareable report** — **"Export report"** saves the selected run(s) as a Markdown or HTML file
  (metadata + a metrics table; a comparison table when several are selected).
- **Completion notifications** — a macOS desktop notification + a sound when a **long** test (≥8 s)
  finishes.
- **Keyboard shortcuts** — Cmd+R (run the active tab's test), Cmd+. / Esc (stop), Cmd+D (detect),
  Cmd+L (list models).
- Fix: the Provider-fit button is now also disabled while a test is running; the status bar's "Ready."
  follows the language choice.

### 2026-07-05 (UI settings)
- **The Help window** (❔ at the top right) — a per-tab guide + tips, an infrastructure line and a
  support contact (a button copies the address). The content is bilingual.
- **Theme selection** — System / Light / Dark, applied immediately. The CustomTkinter widgets update
  themselves; `_retheme()` refreshes the palette on the custom tk charts, the `LiveLog`s and the ttk
  tables. Restored on launch.
- **Language selection** — English (primary) / Estonian. A light `L()` helper (the English string is
  the key; untranslated → an English fallback), applied centrally in `_section` and `_lbl`; tab names,
  sections, field labels, buttons and checkboxes are translated. Switching language rebuilds the tabs
  (blocked while a test is running; the history and the connection fields survive). The ⓘ help texts
  and long descriptions stay English.
- **Settings persistence** — a new `store` settings table (`get_setting`/`set_setting`); the theme and
  language are saved under `~/.llmscanner`.

### 2026-07-04
- **Provider fit** — a new tab that judges whether a backend can take OpenRouter/HuggingFace traffic
  and where it breaks first:
  - **API-contract compliance** (14 checks) — streaming, usage accounting, max_tokens, stop,
    determinism, sampling parameters, concurrent correctness, clean error codes, tool calling,
    structured output, `/v1/models` metadata (pricing + context_length), API-key auth enforcement.
  - **Integrity tests** — token-count honesty (a hard OpenRouter block against billing inflation),
    context honesty (needle-in-a-haystack), model quality (a golden-answer eval), client-cancellation
    handling, logprob fingerprint.
  - **Concurrency sweep** — the throughput knee + a bottleneck classification (prefill/decode/
    batching/admission/stability), p95 **and p99** latency.
  - **A verdict per provider** (FIT / BORDERLINE / NOT FIT), following their documentation (e.g. HF's
    5 s TTFT threshold). The result is stored in History.
  - The client gained `finish_reason`, `stop`/`top_p`/`seed`/`logprobs` pass-through, `stream_chunks`,
    `logprob_avg`, `stream_abort()` and `list_models_raw()` — all backwards compatible.
- **Model fit** wired into History/comparison (overall fit %, the change between runs).
- **A standalone macOS executable** — `build_macos.sh` + `app_entry.py` → `dist/LLMScanner`
  (PyInstaller `--onefile`, the icon generated at runtime).
- **`run.sh` / `run.command`** — one-command launch (creates the venv + installs on first run).

### 2026-07-03
- **Soak test** — a new tab: holds a sustained load for N minutes and measures **tokens per hour**;
  supports replaying a real production workload mix and an **overload probe** (+10%, admission control).
- Marking token counts as estimated when the server sends no usage block.
- Marking under-generation when the server ignores `ignore_eos`.
- Defaults: concurrency ceiling 64, requests-per-worker 2, max-context ceiling 65536, settle pause 3 s.

### 2026-07-02
- **Initial release** — LLM Scanner: discovering, benchmarking and tuning local LLM servers (vLLM,
  SGLang, Ollama, llama.cpp, TGI, LM Studio) with a Mac-friendly GUI
  (Connection / Benchmark / Optimum finder / Network scan / History).
- **A Cancel button** to interrupt the Optimum finder's test.
