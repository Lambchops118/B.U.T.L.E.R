"""Tkinter control panel for the TALOS launcher.

A small settings-and-log window: toggle which components start, choose the
Ollama model and GPU assignments, enable/disable awareness MQTT, then Start.
Child-process output streams into the log pane. Settings that map to
``settings.env`` (Ollama model, MQTT) are written back in place; launcher-only
choices go to ``launcher.config.json``.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import config
from .config import LauncherConfig
from .core import Supervisor


def _ollama_models() -> list[str]:
    """Best-effort list of locally installed Ollama models for the dropdown."""

    if not shutil.which("ollama"):
        return []
    try:
        out = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    models: list[str] = []
    for line in out.splitlines()[1:]:  # skip header
        name = line.split()[0] if line.split() else ""
        if name:
            models.append(name)
    return models


class LauncherGUI:
    # (display label, TALOS_LLM_THINK_MODE value). Only Qwen-family local models
    # (e.g. mb-core-v1) act on these soft switches; other models ignore them, so
    # keep "Off" for Hermes/Llama and hosted API models.
    THINK_MODES = [
        ("Auto — think on complex requests", "auto"),
        ("Always — thinking response", "always"),
        ("Never — instant response", "never"),
        ("Off — model has no thinking mode", "off"),
    ]

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = LauncherConfig.load()
        self.gpus = config.detect_gpus()
        self.supervisor: Supervisor | None = None
        self._log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()

        root.title("TALOS Launcher")
        root.geometry("860x680")
        root.minsize(720, 560)

        self._build_widgets()
        self._load_into_widgets()
        self.root.after(100, self._drain_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ------------------------------------------------------------

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        columns = ttk.Frame(outer)
        columns.pack(fill="x")

        left = ttk.Frame(columns)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(columns)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        # --- Components ---------------------------------------------------
        comp = ttk.LabelFrame(left, text="Components to start", padding=8)
        comp.pack(fill="x")
        self.var_ollama = tk.BooleanVar()
        self.var_awareness_db = tk.BooleanVar()
        self.var_awareness = tk.BooleanVar()
        self.var_main = tk.BooleanVar()
        self.var_voice = tk.BooleanVar()
        self.var_discord = tk.BooleanVar()
        ttk.Checkbutton(comp, text="Ollama (LLM server)", variable=self.var_ollama).pack(anchor="w")
        ttk.Checkbutton(comp, text="Awareness DB (Docker Postgres)", variable=self.var_awareness_db).pack(anchor="w")
        ttk.Checkbutton(comp, text="Awareness backend", variable=self.var_awareness).pack(anchor="w")
        ttk.Checkbutton(comp, text="Main agent", variable=self.var_main).pack(anchor="w")
        ttk.Checkbutton(comp, text="Voice worker", variable=self.var_voice).pack(anchor="w")
        ttk.Checkbutton(comp, text="Discord voice frontend", variable=self.var_discord).pack(anchor="w")

        # --- Options ------------------------------------------------------
        opts = ttk.LabelFrame(left, text="Options", padding=8)
        opts.pack(fill="x", pady=(8, 0))
        self.var_manage_ollama = tk.BooleanVar()
        self.var_manage_docker = tk.BooleanVar()
        self.var_run_migrations = tk.BooleanVar()
        ttk.Checkbutton(opts, text="Start Ollama if not already running", variable=self.var_manage_ollama).pack(anchor="w")
        ttk.Checkbutton(opts, text="Manage Docker Postgres (compose up)", variable=self.var_manage_docker).pack(anchor="w")
        ttk.Checkbutton(opts, text="Run DB migrations before serving", variable=self.var_run_migrations).pack(anchor="w")

        # --- LLM / model --------------------------------------------------
        llm = ttk.LabelFrame(right, text="Language model", padding=8)
        llm.pack(fill="x")
        self.var_use_api = tk.BooleanVar()
        ttk.Checkbutton(
            llm,
            text="Use hosted API models (OpenAI) instead of local",
            variable=self.var_use_api,
            command=self._on_toggle_api,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(llm, text="Local model (Ollama → 5080):").grid(row=1, column=0, sticky="w")
        self.var_model = tk.StringVar()
        models = _ollama_models()
        self.model_box = ttk.Combobox(llm, textvariable=self.var_model, values=models, width=26)
        self.model_box.grid(row=1, column=1, sticky="we", padx=4, pady=2)
        ttk.Label(llm, text="Local base URL:").grid(row=2, column=0, sticky="w")
        self.var_base_url = tk.StringVar()
        self.base_url_entry = ttk.Entry(llm, textvariable=self.var_base_url, width=28)
        self.base_url_entry.grid(row=2, column=1, sticky="we", padx=4, pady=2)
        ttk.Label(llm, text="API model (OpenAI):").grid(row=3, column=0, sticky="w")
        self.var_api_model = tk.StringVar()
        self.api_model_box = ttk.Combobox(
            llm,
            textvariable=self.var_api_model,
            values=["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
            width=26,
        )
        self.api_model_box.grid(row=3, column=1, sticky="we", padx=4, pady=2)

        ttk.Label(llm, text="Thinking:").grid(row=4, column=0, sticky="w")
        self.var_think = tk.StringVar()
        self.think_box = ttk.Combobox(
            llm,
            textvariable=self.var_think,
            values=[label for label, _ in self.THINK_MODES],
            state="readonly",
            width=26,
        )
        self.think_box.grid(row=4, column=1, sticky="we", padx=4, pady=2)
        llm.columnconfigure(1, weight=1)

        # --- Awareness / MQTT --------------------------------------------
        mqtt = ttk.LabelFrame(right, text="Awareness / MQTT", padding=8)
        mqtt.pack(fill="x", pady=(8, 0))
        self.var_mqtt = tk.BooleanVar()
        ttk.Checkbutton(mqtt, text="MQTT enabled", variable=self.var_mqtt).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(mqtt, text="Broker host:").grid(row=1, column=0, sticky="w")
        self.var_mqtt_host = tk.StringVar()
        ttk.Entry(mqtt, textvariable=self.var_mqtt_host, width=20).grid(row=1, column=1, sticky="we", padx=4, pady=2)
        ttk.Label(mqtt, text="Broker port:").grid(row=2, column=0, sticky="w")
        self.var_mqtt_port = tk.StringVar()
        ttk.Entry(mqtt, textvariable=self.var_mqtt_port, width=20).grid(row=2, column=1, sticky="we", padx=4, pady=2)
        mqtt.columnconfigure(1, weight=1)

        # --- GPU assignment ----------------------------------------------
        gpu = ttk.LabelFrame(right, text="GPU assignment", padding=8)
        gpu.pack(fill="x", pady=(8, 0))
        gpu_choices = self._gpu_choices()
        ttk.Label(gpu, text="LLM (Ollama):").grid(row=0, column=0, sticky="w")
        self.var_llm_gpu = tk.StringVar()
        self.llm_gpu_box = ttk.Combobox(gpu, textvariable=self.var_llm_gpu, values=gpu_choices, state="readonly", width=32)
        self.llm_gpu_box.grid(row=0, column=1, sticky="we", padx=4, pady=2)
        ttk.Label(gpu, text="STT (voice):").grid(row=1, column=0, sticky="w")
        self.var_stt_gpu = tk.StringVar()
        self.stt_gpu_box = ttk.Combobox(gpu, textvariable=self.var_stt_gpu, values=gpu_choices, state="readonly", width=32)
        self.stt_gpu_box.grid(row=1, column=1, sticky="we", padx=4, pady=2)
        gpu.columnconfigure(1, weight=1)

        # --- Buttons ------------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 6))
        self.btn_save = ttk.Button(buttons, text="Save settings", command=self._on_save)
        self.btn_save.pack(side="left")
        self.btn_start = ttk.Button(buttons, text="Start TALOS", command=self._on_start)
        self.btn_start.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(buttons, text="Stop", command=self._on_stop, state="disabled")
        self.btn_stop.pack(side="left")
        self.btn_clear = ttk.Button(buttons, text="Clear memory…", command=self._on_clear_memory)
        self.btn_clear.pack(side="left", padx=6)
        self.status = ttk.Label(buttons, text="idle")
        self.status.pack(side="right")

        # --- Log pane -----------------------------------------------------
        logframe = ttk.LabelFrame(outer, text="Logs", padding=4)
        logframe.pack(fill="both", expand=True)
        self.log_text = tk.Text(logframe, wrap="none", height=16, bg="#101418", fg="#d0d0d0", insertbackground="#d0d0d0")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(logframe, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")

    def _gpu_choices(self) -> list[str]:
        choices = [f"{g.index}: {g.name}" for g in self.gpus]
        choices.append("-1: (no pin — all GPUs)")
        return choices

    def _gpu_label_for(self, index: int) -> str:
        for g in self.gpus:
            if g.index == index:
                return f"{g.index}: {g.name}"
        if index < 0:
            return "-1: (no pin — all GPUs)"
        return f"{index}: (index {index})"

    @staticmethod
    def _gpu_index_from(label: str) -> int:
        try:
            return int(label.split(":", 1)[0].strip())
        except (ValueError, IndexError):
            return -1

    def _think_label_for(self, value: str) -> str:
        for label, val in self.THINK_MODES:
            if val == value:
                return label
        return self.THINK_MODES[0][0]

    def _think_value_from(self, label: str) -> str:
        for lbl, val in self.THINK_MODES:
            if lbl == label:
                return val
        return "auto"

    # -- state <-> widgets -------------------------------------------------

    def _load_into_widgets(self) -> None:
        self.var_ollama.set(self.cfg.start_ollama)
        self.var_awareness_db.set(self.cfg.start_awareness_db)
        self.var_awareness.set(self.cfg.start_awareness)
        self.var_main.set(self.cfg.start_main)
        self.var_voice.set(self.cfg.start_voice)
        self.var_discord.set(self.cfg.start_discord)
        self.var_manage_ollama.set(self.cfg.manage_ollama)
        self.var_manage_docker.set(self.cfg.manage_docker)
        self.var_run_migrations.set(self.cfg.run_migrations)
        self.var_llm_gpu.set(self._gpu_label_for(self.cfg.llm_gpu_index))
        self.var_stt_gpu.set(self._gpu_label_for(self.cfg.stt_gpu_index))

        self.var_model.set(config.get_setting("TALOS_LLM_MODEL"))
        self.var_base_url.set(config.get_setting("TALOS_LLM_BASE_URL"))
        self.var_use_api.set(self.cfg.use_api_models)
        self.var_api_model.set(self.cfg.api_llm_model)
        self.var_think.set(self._think_label_for(config.get_setting("TALOS_LLM_THINK_MODE")))
        self.var_mqtt.set(config.setting_bool("TALOS_AWARENESS_MQTT_ENABLED", True))
        self.var_mqtt_host.set(config.get_setting("TALOS_AWARENESS_MQTT_HOST"))
        self.var_mqtt_port.set(config.get_setting("TALOS_AWARENESS_MQTT_PORT"))
        self._on_toggle_api()

    def _collect_config(self) -> LauncherConfig:
        self.cfg.start_ollama = self.var_ollama.get()
        self.cfg.start_awareness_db = self.var_awareness_db.get()
        self.cfg.start_awareness = self.var_awareness.get()
        self.cfg.start_main = self.var_main.get()
        self.cfg.start_voice = self.var_voice.get()
        self.cfg.start_discord = self.var_discord.get()
        self.cfg.manage_ollama = self.var_manage_ollama.get()
        self.cfg.manage_docker = self.var_manage_docker.get()
        self.cfg.run_migrations = self.var_run_migrations.get()
        self.cfg.llm_gpu_index = self._gpu_index_from(self.var_llm_gpu.get())
        self.cfg.stt_gpu_index = self._gpu_index_from(self.var_stt_gpu.get())
        self.cfg.use_api_models = self.var_use_api.get()
        self.cfg.api_llm_model = self.var_api_model.get().strip() or "gpt-4o-mini"
        return self.cfg

    def _persist(self) -> None:
        self._collect_config().save()
        # settings.env (only write the launcher-editable keys).
        config.set_setting("TALOS_LLM_MODEL", self.var_model.get().strip())
        config.set_setting("TALOS_LLM_BASE_URL", self.var_base_url.get().strip())
        config.set_setting("TALOS_LLM_THINK_MODE", self._think_value_from(self.var_think.get()))
        config.set_setting("TALOS_AWARENESS_MQTT_ENABLED", "1" if self.var_mqtt.get() else "0")
        config.set_setting("TALOS_AWARENESS_MQTT_HOST", self.var_mqtt_host.get().strip())
        config.set_setting("TALOS_AWARENESS_MQTT_PORT", self.var_mqtt_port.get().strip())

    # -- actions -----------------------------------------------------------

    def _on_save(self) -> None:
        try:
            self._persist()
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self._append_log("launcher", "settings saved.")
        self.status.config(text="saved")

    def _on_toggle_api(self) -> None:
        """Enable/disable model fields to reflect the API-vs-local choice."""

        api = self.var_use_api.get()
        local_state = "disabled" if api else "normal"
        api_state = "normal" if api else "disabled"
        self.model_box.configure(state=local_state)
        self.base_url_entry.configure(state=local_state)
        self.api_model_box.configure(state=api_state)
        # Hosted models ignore the Qwen think switches; the launcher forces "off".
        self.think_box.configure(state="disabled" if api else "readonly")
        if api and not config.has_secret("OPENAI_API_KEY"):
            self._append_log(
                "launcher",
                "WARNING: OPENAI_API_KEY not found in .env — hosted API calls will fail.",
            )

    def _on_clear_memory(self) -> None:
        if self.supervisor and self.supervisor.is_running():
            messagebox.showwarning(
                "Stop TALOS first",
                "Stop TALOS before clearing memory — the conversation store is "
                "held open by the running main agent.",
            )
            return
        if not messagebox.askyesno(
            "Clear all memory",
            "This PERMANENTLY deletes:\n\n"
            "  • the awareness system's long-term memory\n"
            "  • the persistent conversation store (facts, summaries, history)\n\n"
            "Presence, state, history, and alerts are NOT affected. This cannot "
            "be undone. Proceed?",
            icon="warning",
            default="no",
        ):
            return
        self.btn_clear.config(state="disabled")
        self.status.config(text="clearing memory...")
        threading.Thread(target=self._clear_worker, daemon=True).start()

    def _clear_worker(self) -> None:
        from . import maintenance

        try:
            summary = maintenance.clear_all_memory(
                log=self._enqueue_log,
                ensure_db=self.var_manage_docker.get(),
            )
            self.root.after(0, lambda: self._on_clear_done(summary, None))
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self.root.after(0, lambda: self._on_clear_done(None, exc))

    def _on_clear_done(self, summary: str | None, error: Exception | None) -> None:
        self.btn_clear.config(state="normal")
        if error is not None:
            self.status.config(text="clear failed")
            self._append_log("clear", f"failed: {error}")
            messagebox.showerror("Clear memory failed", str(error))
        else:
            self.status.config(text="memory cleared")
            self._append_log("clear", summary or "done")

    def _on_start(self) -> None:
        if self.supervisor and self.supervisor.is_running():
            messagebox.showinfo("Already running", "TALOS is already running.")
            return
        try:
            self._persist()
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.supervisor = Supervisor(self.cfg, log=self._enqueue_log)
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status.config(text="starting...")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        try:
            assert self.supervisor is not None
            self.supervisor.start()
            self.root.after(0, lambda: self.status.config(text="running"))
        except Exception as exc:  # noqa: BLE001 - surface any startup failure
            self.root.after(0, lambda: self._on_start_failed(exc))

    def _on_start_failed(self, exc: Exception) -> None:
        self._append_log("launcher", f"start failed: {exc}")
        self.status.config(text="error")
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _on_stop(self) -> None:
        self.status.config(text="stopping...")
        self.btn_stop.config(state="disabled")
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self) -> None:
        if self.supervisor:
            self.supervisor.stop()
        self.root.after(0, self._on_stopped)

    def _on_stopped(self) -> None:
        self.status.config(text="idle")
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _on_close(self) -> None:
        if self.supervisor and self.supervisor.is_running():
            if not messagebox.askyesno("Quit", "Stop all TALOS processes and quit?"):
                return
            self.supervisor.stop()
        self.root.destroy()

    # -- logging -----------------------------------------------------------

    def _enqueue_log(self, source: str, message: str) -> None:
        self._log_queue.put((source, message))

    def _drain_log_queue(self) -> None:
        try:
            while True:
                source, message = self._log_queue.get_nowait()
                self._append_log(source, message)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _append_log(self, source: str, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{source}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    LauncherGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
