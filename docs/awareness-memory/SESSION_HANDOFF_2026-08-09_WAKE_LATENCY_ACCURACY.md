# Session Handoff — Wake Latency and Accuracy Recovery

Session goal: Implement recommendations 1, 2, and the accuracy-safe parts of 5 from the wake-word regression analysis for commit `e33c2f1`.

Current phase: Bounded post-Phase-F voice correction complete; owner-visible idle-VAD corpus acceptance pending.

Bounded task completed: Restored SpeechRecognition as the production idle utterance segmenter without restoring duplicate transcription; added independent idle/barge-in VAD lanes; gated idle VAD behind both an enable request and explicit corpus acceptance; increased idle pre-roll to 640 ms; preserved utterances across playback boundaries; asynchronously preloaded faster-whisper; and serialized local ASR through a bounded priority queue that selects fresh idle commands before queued barge-in confirmations.

Files added: `talos/voice/asr_queue.py`, `tests/test_voice_asr_queue.py`, and this handoff.

Files modified: `settings.env`, voice agent, faster-whisper backend, VAD gate, focused tests, README, implementation status, architecture decisions, and open questions.

Migrations added: None.

Decisions made: ADR-025. SpeechRecognition segmentation is not a second transcription pass. Experimental idle VAD cannot activate unless both `TALOS_IDLE_VAD_ENDPOINTING=1` and `TALOS_IDLE_VAD_CORPUS_ACCEPTED=1`.

Assumptions confirmed or changed: The previous 320 ms VAD pre-roll provided only 224 ms before the first high-probability frame. The generic and production barge-in defaults now retain 500 ms; the independent idle lane retains 640 ms. Idle and barge-in have different error costs and no longer share capture state or thresholds.

Tests run: Focused unit tests for barge-in policy, VAD, faster-whisper, ASR priority, fixture acceptance, streaming speaker, and duplex audio; targeted `py_compile`; `git diff --check`.

Tests passed: 70 focused tests; targeted syntax compilation; `git diff --check`.

Tests failed: None in the final 70-test focused run. An expanded 95-test sweep passed 91 and hit four sandbox-only permission errors in pre-existing fixture-recorder tests that create nested `TemporaryDirectory` paths; moving `TEMP` to the writable visualization root produced the same sandbox denial. An earlier diagnostic attempt also polluted Python 3.12 with Python 3.9's NumPy through `PYTHONPATH` and produced six environment-only import errors; the clean run imported bundled NumPy first. SpeechRecognition available to the clean diagnostic run was 3.8.1 rather than the voice environment's pinned 3.14.3.

Commands not run: No live microphone/AEC/wake-word session, owner-room corpus, trailing-silence tuning, multi-volume matrix, device restart, or eight-hour soak. No raw room audio was recorded.

Known limitations: Faster-whisper remains a finished-utterance batch backend. Speculative chunk decoding was not added because it would reintroduce redundant inference and has no accuracy evidence; true incremental decoding needs a supported streaming backend and its own accepted corpus. The independent idle endpoint retains the conservative 480 ms trailing-silence setting until real command-pause evidence exists.

Security implications: Audio remains local-first. No new upload path or action authority was added. Room recording remains explicit and disabled by default.

Deployment implications: Restart the voice worker to pick up `TALOS_VAD_ENDPOINTING=0`. Production uses SpeechRecognition segmentation immediately. The new idle Silero lane remains inactive under tracked settings. Faster-whisper begins loading asynchronously during voice-worker startup.

Unresolved questions: OQ-I (barge-in corpus/soak) and OQ-J (independent idle-VAD wake/pause/noise acceptance).

Current repository state: Requested changes are unstaged. The pre-existing modified `Peripherals/Pump-Power-Controller` submodule was not touched. `settings.env` already had `TALOS_BARGE_IN=1` before this task, which conflicts with ADR-024's documented rollout posture and remains an owner deployment decision outside this bounded request.

Next permitted task: Run an owner-visible corpus comparing production SpeechRecognition segmentation with independent idle VAD across repeated "Butler" commands, natural pauses, distances, volumes, noise, and negative phrases. Tune idle trailing silence only from those results, then set the acceptance flag only if the matrix passes.

Required reading for next session: This handoff, `IMPLEMENTATION_STATUS.md`, `docs/voice/BARGE_IN_REDESIGN_PLAN.md`, `talos/voice/agent.py`, `talos/voice/asr_queue.py`, `talos/voice/streaming/vad.py`, and `settings.env`.

Explicit stop point: Do not set `TALOS_IDLE_VAD_CORPUS_ACCEPTED=1`, claim incremental decoding, change barge-in rollout state, or silently collect room audio before owner-visible evidence and approval.
