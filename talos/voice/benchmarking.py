import csv
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
RUN_STARTED_AT = datetime.now().astimezone()
# Single rolling log so every run appends to one readable file. The
# ``run_started_at`` column still distinguishes individual runs. (Historically a
# new timestamped file was created per process start, which fragmented the data
# across dozens of files and made it look like recording had stopped.)
CSV_LOG_PATH = LOG_DIR / "voice_benchmarks.csv"

_LOG_LOCK = threading.Lock()

TIMESTAMP_COLUMNS = [
    "callback_started",
    "recording_started_est",
    "recording_ended_est",
    "command_start",
    "local_wake_send",
    "local_wake_done",
    "stt_send",
    "stt_done",
    "llm_send",
    "llm_first_done",
    "llm_followup_send",
    "llm_done",
    "polly_send",
    "polly_done",
    "audio_open_start",
    "audio_stream_ready",
    "first_audio",
    "pipeline_done",
]

METRIC_COLUMNS = [
    "input_rms",
    "recording_duration_ms",
    "recording_start_to_wake_word_ms",
    "recording_start_to_wake_word_end_ms",
    "wake_word_to_recording_start_ms",
    "local_wake_latency_ms",
    "speech_to_text_latency_ms",
    "llm_ttft_ms",
    "llm_initial_latency_ms",
    "llm_followup_latency_ms",
    "llm_total_latency_ms",
    "llm_request_count",
    "aws_polly_latency_ms",
    "audio_file_open_latency_ms",
    "mp3_open_latency_ms",
    "total_end_of_speech_to_first_audio_ms",
    "stt_model_load_ms",
    "prompt_tokens_estimated",
    "provider_prompt_tokens",
    "provider_completion_tokens",
    "provider_total_tokens",
    "llm_model_load_ms",
    "ollama_context_length",
    "awareness_snapshot_ms",
    "tool_build_ms",
    "memory_context_ms",
    "prompt_assembly_ms",
    "tool_execution_total_ms",
    "agent_stream_total_ms",
    "text_stream_total_ms",
    "polly_request_count",
    "polly_total_ms",
    "audio_write_total_ms",
    "voice_pipeline_total_ms",
]

DIMENSION_COLUMNS = [
    "stt_backend",
    "llm_backend",
    "llm_backend_location",
    "llm_model",
    "llm_fallback_used",
    "llm_model_load_measurement",
]

# Step durations (milliseconds). These lead the metrics so the most-read numbers
# sit right after the interaction text. Non-duration metrics (rms, counts) trail.
DURATION_COLUMNS = [name for name in METRIC_COLUMNS if name.endswith("_ms")]
OTHER_METRIC_COLUMNS = [name for name in METRIC_COLUMNS if not name.endswith("_ms")]

CSV_COLUMNS = [
    # Lead with the interaction context, then every step duration in ms.
    "run_started_at",
    "session_id",
    "command",
    "transcript",
    "response_preview",
    *DURATION_COLUMNS,
    # Everything else follows.
    "reason",
    "wake_word",
    "wake_word_mode",
    *DIMENSION_COLUMNS,
    *OTHER_METRIC_COLUMNS,
    *[f"ts_{name}" for name in TIMESTAMP_COLUMNS],
    "llm_ttft_note",
    "mp3_open_note",
    "notes",
    "errors",
    "csv_file",
]


def _wall_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")


def _ms(delta_seconds: Optional[float]) -> Optional[float]:
    if delta_seconds is None:
        return None
    return round(delta_seconds * 1000.0, 1)


def _preview(text: Optional[str], limit: int = 160) -> Optional[str]:
    if not text:
        return text
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _norm_token(text: Optional[str]) -> str:
    if not text:
        return ""
    return "".join(ch for ch in text.lower() if ch.isalnum())


@dataclass
class VoiceBenchmarkSession:
    wake_word: str
    wake_word_mode: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    stages_wall: dict[str, float] = field(default_factory=dict)
    stages_mono: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    dimensions: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    command: Optional[str] = None
    transcript: Optional[str] = None
    response_text: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _emitted: bool = field(default=False, repr=False)

    def mark_stage(self, name: str, *, wall_ts: Optional[float] = None, mono_ts: Optional[float] = None) -> None:
        if wall_ts is None:
            wall_ts = time.time()
        if mono_ts is None:
            mono_ts = time.perf_counter()
        with self._lock:
            self.stages_wall[name] = wall_ts
            self.stages_mono[name] = mono_ts

    def set_metric(self, key: str, value: Any) -> None:
        with self._lock:
            self.metrics[key] = value

    def add_metric(self, key: str, value: float) -> None:
        with self._lock:
            current = self.metrics.get(key) or 0.0
            self.metrics[key] = round(float(current) + float(value), 1)

    def set_dimension(self, key: str, value: Any) -> None:
        with self._lock:
            self.dimensions[key] = value

    def apply_pipeline_telemetry(self, payload: dict[str, Any]) -> None:
        """Merge bounded main-process SSE telemetry into this voice request."""
        if not isinstance(payload, dict):
            return
        event = str(payload.get("event") or "")
        dimension_map = {
            "backend": "llm_backend",
            "backend_location": "llm_backend_location",
            "model": "llm_model",
            "model_load_measurement": "llm_model_load_measurement",
        }
        metric_map = {
            "prompt_tokens_estimated": "prompt_tokens_estimated",
            "provider_prompt_tokens": "provider_prompt_tokens",
            "provider_completion_tokens": "provider_completion_tokens",
            "provider_total_tokens": "provider_total_tokens",
            "model_load_ms": "llm_model_load_ms",
            "ollama_context_length": "ollama_context_length",
            "awareness_snapshot_ms": "awareness_snapshot_ms",
            "tool_build_ms": "tool_build_ms",
            "memory_context_ms": "memory_context_ms",
            "prompt_assembly_ms": "prompt_assembly_ms",
            "tool_execution_total_ms": "tool_execution_total_ms",
            "text_stream_total_ms": "text_stream_total_ms",
            "agent_stream_total_ms": "agent_stream_total_ms",
            "llm_ttft_ms": "llm_ttft_ms",
        }
        for source, target in dimension_map.items():
            value = payload.get(source)
            if value is not None:
                self.set_dimension(target, value)
        for source, target in metric_map.items():
            value = payload.get(source)
            if value is not None:
                self.set_metric(target, value)
        if event == "llm_round_failed":
            exact = payload.get("provider_prompt_tokens")
            if exact is not None:
                self.set_metric("provider_prompt_tokens", exact)

    def pipeline_snapshot(self) -> dict[str, Any]:
        return self._snapshot()

    def add_note(self, note: str) -> None:
        if not note:
            return
        with self._lock:
            self.notes.append(note)

    def add_error(self, error: str) -> None:
        if not error:
            return
        with self._lock:
            self.errors.append(error)

    def set_command(self, command: str) -> None:
        with self._lock:
            self.command = command

    def set_transcript(self, transcript: str) -> None:
        with self._lock:
            self.transcript = transcript

    def set_response_text(self, response_text: str) -> None:
        with self._lock:
            self.response_text = response_text

    def note_recording_ready(self, duration_seconds: float) -> None:
        callback_wall = time.time()
        callback_mono = time.perf_counter()
        recording_end_wall = callback_wall
        recording_start_wall = callback_wall - duration_seconds
        recording_end_mono = callback_mono
        recording_start_mono = callback_mono - duration_seconds

        with self._lock:
            self.stages_wall["callback_started"] = callback_wall
            self.stages_mono["callback_started"] = callback_mono
            self.stages_wall["recording_started_est"] = recording_start_wall
            self.stages_mono["recording_started_est"] = recording_start_mono
            self.stages_wall["recording_ended_est"] = recording_end_wall
            self.stages_mono["recording_ended_est"] = recording_end_mono
            self.metrics["recording_duration_ms"] = _ms(duration_seconds)

    def note_wake_word_offsets(self, transcript_words: Optional[list[Any]]) -> None:
        if not transcript_words:
            self.add_note("Wake-word word timing unavailable from transcription response.")
            return

        wake_token = _norm_token(self.wake_word)
        for item in transcript_words:
            word = getattr(item, "word", None)
            start = getattr(item, "start", None)
            end = getattr(item, "end", None)
            if isinstance(item, dict):
                word = item.get("word", word)
                start = item.get("start", start)
                end = item.get("end", end)

            if _norm_token(word) != wake_token:
                continue

            if start is not None:
                self.set_metric("recording_start_to_wake_word_ms", _ms(float(start)))
            if end is not None:
                self.set_metric("recording_start_to_wake_word_end_ms", _ms(float(end)))
            self.add_note(
                "Wake-word-to-recording-start is not a separate latency in the current single-utterance capture flow."
            )
            return

        self.add_note("Wake word not located in word-level transcription timings.")

    def _snapshot(self) -> dict[str, Any]:
        with self._lock:
            stages_wall = dict(self.stages_wall)
            stages_mono = dict(self.stages_mono)
            metrics = dict(self.metrics)
            dimensions = dict(self.dimensions)
            notes = list(self.notes)
            errors = list(self.errors)
            command = self.command
            transcript = self.transcript
            response_text = self.response_text

        def delta_ms(start: str, end: str) -> Optional[float]:
            if start not in stages_mono or end not in stages_mono:
                return None
            return _ms(stages_mono[end] - stages_mono[start])

        metrics.setdefault("speech_to_text_latency_ms", delta_ms("stt_send", "stt_done"))
        metrics.setdefault("local_wake_latency_ms", delta_ms("local_wake_send", "local_wake_done"))
        metrics.setdefault("llm_initial_latency_ms", delta_ms("llm_send", "llm_first_done"))
        metrics.setdefault("llm_followup_latency_ms", delta_ms("llm_followup_send", "llm_done"))
        metrics.setdefault("llm_total_latency_ms", delta_ms("llm_send", "llm_done"))
        metrics.setdefault("aws_polly_latency_ms", delta_ms("polly_send", "polly_done"))
        metrics.setdefault("audio_file_open_latency_ms", delta_ms("audio_open_start", "audio_stream_ready"))
        metrics.setdefault("mp3_open_latency_ms", metrics.get("audio_file_open_latency_ms"))
        metrics.setdefault("total_end_of_speech_to_first_audio_ms", delta_ms("recording_ended_est", "first_audio"))
        metrics.setdefault("voice_pipeline_total_ms", delta_ms("command_start", "pipeline_done"))
        metrics.setdefault("llm_ttft_ms", None)
        metrics.setdefault("llm_ttft_note", "Unavailable with the current non-streaming OpenAI Responses API call.")
        metrics.setdefault("mp3_open_note", "Current playback pipeline uses a WAV/PCM file synthesized from Polly output.")
        metrics.setdefault(
            "wake_word_to_recording_start_ms",
            None,
        )

        llm_request_count = 0
        if "llm_send" in stages_mono:
            llm_request_count += 1
        if "llm_followup_send" in stages_mono:
            llm_request_count += 1
        metrics.setdefault("llm_request_count", llm_request_count)

        return {
            "session_id": self.session_id,
            "wake_word": self.wake_word,
            "wake_word_mode": self.wake_word_mode,
            "command": command,
            "transcript": transcript,
            "response_preview": _preview(response_text),
            "dimensions": dimensions,
            "timestamps": {name: _wall_iso(ts) for name, ts in sorted(stages_wall.items())},
            "latencies_ms": metrics,
            "notes": notes,
            "errors": errors,
        }

    @staticmethod
    def _is_meaningful(payload: dict[str, Any]) -> bool:
        """A row is worth logging when something was actually said or done.

        Keeps real commands and general talking (any non-empty transcript), plus
        anything that carried an error (useful for diagnostics). Excludes blank
        clips where the model heard nothing / only noise -- e.g. ``discarded_audio``
        and ``empty_transcript`` -- which otherwise flood the log.
        """
        return bool(
            payload.get("command")
            or payload.get("transcript")
            or payload.get("response_preview")
            or payload.get("errors")
        )

    def emit_summary_once(self, reason: str) -> dict[str, Any]:
        already_emitted = False
        with self._lock:
            already_emitted = self._emitted
            if not self._emitted:
                self._emitted = True

        if already_emitted:
            return self._snapshot()

        payload = self._snapshot()
        payload["reason"] = reason

        line = self._format_summary_line(payload)
        if self._is_meaningful(payload):
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with _LOG_LOCK:
                self._append_csv_row(payload)
        else:
            line += " | csv=skipped(blank)"

        print(line)
        return payload

    def _append_csv_row(self, payload: dict[str, Any]) -> None:
        lat = payload["latencies_ms"]
        row = {
            "run_started_at": RUN_STARTED_AT.isoformat(timespec="seconds"),
            "csv_file": str(CSV_LOG_PATH),
            "reason": payload.get("reason"),
            "session_id": payload.get("session_id"),
            "wake_word": payload.get("wake_word"),
            "wake_word_mode": payload.get("wake_word_mode"),
            "command": payload.get("command"),
            "transcript": payload.get("transcript"),
            "response_preview": payload.get("response_preview"),
            "llm_ttft_note": lat.get("llm_ttft_note"),
            "mp3_open_note": lat.get("mp3_open_note"),
            "notes": " | ".join(payload.get("notes") or []),
            "errors": " | ".join(payload.get("errors") or []),
        }

        for name in DIMENSION_COLUMNS:
            row[name] = payload.get("dimensions", {}).get(name)

        for name in TIMESTAMP_COLUMNS:
            row[f"ts_{name}"] = payload["timestamps"].get(name)

        for name in METRIC_COLUMNS:
            row[name] = lat.get(name)

        write_header = not CSV_LOG_PATH.exists()
        with CSV_LOG_PATH.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def _format_summary_line(self, payload: dict[str, Any]) -> str:
        lat = payload["latencies_ms"]
        ts = payload["timestamps"]
        parts = [
            f"[voice-bench {payload['session_id']}]",
            f"command={json.dumps(payload.get('command') or '', ensure_ascii=True)}",
            f"recording={lat.get('recording_duration_ms')}ms",
            f"stt={lat.get('speech_to_text_latency_ms')}ms",
            (
                "llm="
                f"{lat.get('llm_total_latency_ms')}ms"
                f" (requests={lat.get('llm_request_count')}, ttft={lat.get('llm_ttft_ms')})"
            ),
            f"polly={lat.get('aws_polly_latency_ms')}ms",
            f"audio_open={lat.get('audio_file_open_latency_ms')}ms",
            f"end_of_speech_to_first_audio={lat.get('total_end_of_speech_to_first_audio_ms')}ms",
        ]
        dimensions = payload.get("dimensions") or {}
        if dimensions.get("llm_backend"):
            parts.append(
                "backend="
                f"{dimensions.get('llm_backend_location')}:"
                f"{dimensions.get('llm_backend')}/"
                f"{dimensions.get('llm_model')}"
            )
        if lat.get("provider_prompt_tokens") is not None:
            parts.append(f"prompt_tokens={lat.get('provider_prompt_tokens')}")
        elif lat.get("prompt_tokens_estimated") is not None:
            parts.append(f"prompt_tokens_est={lat.get('prompt_tokens_estimated')}")

        if lat.get("recording_start_to_wake_word_ms") is not None:
            parts.append(f"recording_to_wake={lat.get('recording_start_to_wake_word_ms')}ms")
        if ts.get("llm_send"):
            parts.append(f"llm_send={ts.get('llm_send')}")
        parts.append(f"csv={CSV_LOG_PATH.name}")
        if payload.get("errors"):
            parts.append(f"errors={json.dumps(payload['errors'], ensure_ascii=True)}")

        return " | ".join(parts)
