# Session Handoff — Pipeline Telemetry

Session goal:
Investigate the perceived speech/latency regression, review the awareness and full speech-to-LLM-to-output pipeline, answer the owner's focused questions, and implement only correlated pipeline telemetry.

Current phase:
Phases 0–8 remain complete. This was an owner-authorized bounded observability task, not a new numbered phase.

Bounded task completed:
Added telemetry identifying local versus hosted/fallback backends, prompt-token estimates and provider counts when exposed, model-load timing/status, and durations for each instrumented pipeline stage. Events share a request ID across voice, text service, agent runtime, tools, LLM, TTS, and playback. The new JSONL event stream intentionally excludes prompt, transcript, response, and tool-argument content.

Files added:
- `talos/telemetry.py`
- `tests/test_pipeline_telemetry.py`
- `tests/test_service_client_telemetry.py`
- `tests/test_voice_benchmarking.py`
- `docs/awareness-memory/SESSION_HANDOFF_2026-07-24_PIPELINE_TELEMETRY.md`

Files modified:
- `settings.env`
- `talos/agent/runtime.py`
- `talos/text/server.py`
- `talos/text/service_client.py`
- `talos/voice/agent.py`
- `talos/voice/benchmarking.py`
- `talos/voice/backends/base.py`
- `talos/voice/backends/factory.py`
- `talos/voice/backends/llm_openai_compat.py`
- `talos/voice/backends/stt_faster_whisper.py`
- `tests/test_llm_openai_compat.py`
- `tests/test_run_command_stream.py`
- `tests/test_stt_faster_whisper.py`
- `docs/awareness-memory/IMPLEMENTATION_STATUS.md`

Migrations added:
None.

Decisions made:
- Use append-only per-process JSONL with correlated request IDs as the durable telemetry record and SSE telemetry events for the voice/text-process boundary.
- Record prompt tokens as an explicitly labeled conservative estimate unless the provider supplies usage.
- Record exact faster-whisper load duration. For Ollama's OpenAI-compatible stream, report an already-loaded zero measurement when `/api/ps` confirms residency; otherwise report time-to-first-token as a labeled cold-start upper bound because this endpoint does not expose native model `load_duration`.
- Keep telemetry best-effort so logging failure cannot break the assistant pipeline.

Assumptions confirmed or changed:
- `OLLAMA_CONTEXT_LENGTH=16384` is set on the launcher-created `ollama serve` process. It does not reconfigure a server that was already listening before TALOS started.
- No repository rationale was found for choosing `distil-large-v3` specifically. The documented rationale supports moving from hosted Whisper to a local faster-whisper path generally: local/offline operation and eliminating a second transcription request.

Tests run:
- Main Python 3.10 environment: telemetry, LLM backend, agent runtime, text server/client, service-client telemetry, and voice benchmarking focused suites.
- Voice Python 3.12 environment: faster-whisper, sentence chunking, streaming speaker, voice benchmarking, and service-client telemetry focused suites.
- `py_compile` for all changed Python modules in both environments.
- An earlier focused validation pass covered the same implementation before the final test additions.

Tests passed:
- Final focused validation: 51 tests.
- Earlier focused validation: 46 tests.
- Both `py_compile` invocations.

Tests failed:
None.

Commands not run:
- No live Ollama inference/model-residency test; the local Ollama endpoint was not listening during inspection.
- No live microphone, Polly, or speaker-device pipeline test.
- No full repository test suite.

Known limitations:
- Prompt-token telemetry is estimated when streaming providers do not return usage.
- Ollama native model load duration is unavailable through the current OpenAI-compatible streaming response. The cold-load value is therefore explicitly marked as a TTFT upper bound.
- Per-process log files need request-ID correlation when the voice and text services run separately.
- Stage timings add observability but do not themselves correct the suspected regression.

Security implications:
The new JSONL telemetry excludes conversational and tool-argument payloads. It records operational metadata such as model/backend names, counts, durations, request IDs, and error types. Existing legacy benchmark logs retain their pre-existing behavior and were not broadened by this task.

Deployment implications:
Telemetry is enabled by default through `TALOS_PIPELINE_TELEMETRY_ENABLED=1` and writes under `talos/logs` unless `TALOS_PIPELINE_TELEMETRY_DIR` overrides it. The directory must be writable. No schema or service dependency was added.

Unresolved questions:
- A live run is needed to confirm Ollama's actual allocated context length and collect empirical stage timings.
- Whether to add hard prompt preflight, progressively disclose tool schemas, or retune/revert STT remains owner-directed follow-up work.

Current repository state:
The bounded telemetry implementation is complete and focused tests pass. Numerous unrelated/pre-existing working-tree changes remain and were preserved.

Next permitted task:
Owner live voice/Ollama validation using the telemetry. Any behavior or configuration change requires a separately bounded task.

Required reading for next session:
- `docs/awareness-memory/IMPLEMENTATION_STATUS.md`
- This handoff
- `docs/awareness-memory/ARCHITECTURAL_INVARIANTS.md`

Explicit stop point:
Stop after telemetry implementation, focused verification, and this handoff. Do not change STT behavior, context configuration, tool selection, prompt preflight, or awareness behavior as part of this task.
