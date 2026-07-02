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
    """A tiny dependency-free, appearance-aware line chart on a tk.Canvas."""

    def __init__(self, parent, height=160, **kw):
        super().__init__(parent, height=height, bg=_palette()["canvas_bg"],
                         highlightthickness=0, **kw)
        self._points: list[tuple[str, float | None]] = []
        self._title = ""
        self._unit = ""
        self.bind("<Configure>", lambda e: self._redraw())

    def plot(self, points, title="", unit=""):
        self._points = list(points)
        self._title = title
        self._unit = unit
        self._redraw()

    def clear(self):
        self._points = []
        self._title = ""
        self._redraw()

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
        pts = [(i, v) for i, (_lbl, v) in enumerate(self._points) if v is not None]
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
            self.create_text(x0 - 6, yy, anchor="e", text=f"{yv:.0f}",
                             fill=p["sub"], font=("TkDefaultFont", 8))

        coords = []
        for i, v in pts:
            coords += [sx(i), sy(v)]
        if len(coords) >= 4:
            self.create_line(*coords, fill=p["line"], width=2)
        for i, v in pts:
            x, y = sx(i), sy(v)
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=p["line"], outline="")
        li, lv = pts[-1]
        self.create_text(sx(li), sy(lv) - 9, text=f"{lv:.1f}", fill=p["txt"],
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

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

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
        self.pal = _palette()
        self.root.title(APP_TITLE)
        self.root.geometry("1400x1010")
        self.root.minsize(1200, 900)
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
        ctk.CTkLabel(outer, text=title, anchor="w",
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
        ctk.CTkLabel(fr, text=text).pack(side="left")
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

    # ----------------------------------------------------------------- UI build
    def _build_ui(self):
        self.tabview = ctk.CTkTabview(self.root)
        self.tabview.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.tab_conn = self.tabview.add("Connection")
        self.tab_bench = self.tabview.add("Benchmark")
        self.tab_opt = self.tabview.add("Optimum finder")
        self.tab_scan = self.tabview.add("Network scan")
        self.tab_history = self.tabview.add("History")

        self._build_conn_tab()
        self._build_bench_tab()
        self._build_opt_tab()
        self._build_scan_tab()
        self._build_history_tab()

        bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(0, 8))
        self.progress = ctk.CTkProgressBar(bar, mode="indeterminate", width=180)
        self.progress.set(0)
        self.progress.pack(side="right", padx=6)
        self.status = ctk.CTkLabel(bar, text="Ready.", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

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
        ctk.CTkButton(body, text="Load", width=70, command=self.on_load_host).grid(row=0, column=1, padx=4)
        ctk.CTkButton(body, text="Save current…", width=120, command=self.on_save_host).grid(row=0, column=2, padx=4)
        ctk.CTkButton(body, text="Delete", width=70, fg_color="#b04a4a", hover_color="#963c3c",
                      command=self.on_delete_host).grid(row=0, column=3, padx=4)
        self._refresh_hosts()

        self._conn_fields(self.tab_conn)
        self._refresh_host_suggestions()

        btns = ctk.CTkFrame(self.tab_conn, fg_color="transparent")
        btns.pack(fill="x", padx=12)
        self.btn_detect = ctk.CTkButton(btns, text="Detect server", command=self.on_detect)
        self.btn_detect.pack(side="left")
        self.btn_models = ctk.CTkButton(btns, text="List models", command=self.on_list_models)
        self.btn_models.pack(side="left", padx=8)

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
        self.btn_run = ctk.CTkButton(runbar, text="Run benchmark", command=self.on_run_bench)
        self.btn_run.pack(side="left")
        self.btn_repeat = ctk.CTkButton(runbar, text="Repeat last run", state="disabled",
                                        command=self.on_repeat)
        self.btn_repeat.pack(side="left", padx=8)
        ctk.CTkButton(runbar, text="Export CSV…", width=100,
                      command=self.export_bench).pack(side="left", padx=4)
        ctk.CTkButton(runbar, text="Copy to clipboard", width=130,
                      command=self.copy_bench).pack(side="left", padx=4)
        self.btn_clear = ctk.CTkButton(runbar, text="Clear view", fg_color="gray40",
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
        self.btn_opt = ctk.CTkButton(runbar, text="Find optima", command=self.on_run_optima)
        self.btn_opt.pack(side="left")
        btn_opt_cancel = ctk.CTkButton(runbar, text="Cancel", width=80, state="disabled",
                                       fg_color="#b04a4a", hover_color="#963c3c",
                                       command=self.cancel_current)
        btn_opt_cancel.pack(side="left", padx=8)
        self._cancel_btns.append(btn_opt_cancel)
        ctk.CTkButton(runbar, text="Export CSV…", width=100,
                      command=self.export_optima).pack(side="left", padx=8)
        ctk.CTkButton(runbar, text="Copy to clipboard", width=130,
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
        self.btn_scan = ctk.CTkButton(btns, text="Scan network", command=self.on_scan)
        self.btn_scan.pack(side="left")
        ctk.CTkButton(btns, text="Use selected server", command=self.on_use_server).pack(side="left", padx=8)
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
        ctk.CTkButton(bar, text="Refresh", width=80, command=self.refresh_history).pack(side="left")
        ctk.CTkButton(bar, text="Export CSV…", width=100, command=self.export_history).pack(side="left", padx=6)
        ctk.CTkButton(bar, text="Clear all", width=80, fg_color="#b04a4a", hover_color="#963c3c",
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
                     text="Click a heading to sort · drag headings to reorder · drag borders to "
                          "resize · type to filter · select a row to chart it.").pack(
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

        def done_cb(fut):
            try:
                res = fut.result()
                self.post(lambda: self._finish(lambda: on_done(res)))
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
        for log in (getattr(self, "bench_log", None), getattr(self, "opt_log", None)):
            if log is not None:
                log.write("✗ Cancelled by user", "err")

    def _finish(self, fn):
        self._set_busy(False)
        fn()

    # ------------------------------------------------------------- UI helpers
    def _set_busy(self, busy: bool, status: str = ""):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_detect, self.btn_models, self.btn_run, self.btn_scan, self.btn_opt):
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
            self._set_status("Ready.")

    def _set_status(self, text: str):
        self.status.configure(text=text)

    def _error(self, err: Exception):
        self._set_status(f"Error: {err}")
        for log in (getattr(self, "bench_log", None), getattr(self, "opt_log", None)):
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
    ctk.set_appearance_mode("System")     # follows the OS light/dark setting
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
