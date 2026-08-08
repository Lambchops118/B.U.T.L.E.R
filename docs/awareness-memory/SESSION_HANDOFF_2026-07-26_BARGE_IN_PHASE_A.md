# Session Handoff — Barge-In Redesign Phase A

Session goal: Implement the next bounded phase in
`docs/voice/BARGE_IN_REDESIGN_PLAN.md`.

Current phase: Barge-in redesign Phase A — Containment and measurement.

Bounded task completed: Yes. Phase A is complete and the work stopped before
the Phase B AEC feasibility spike.

Files added:

- `talos/voice/streaming/barge_in_observability.py`
- `tests/test_barge_in_observability.py`
- `docs/awareness-memory/SESSION_HANDOFF_2026-07-26_BARGE_IN_PHASE_A.md`

Files modified:

- `talos/voice/streaming/barge_in.py`
- `talos/voice/agent.py`
- `tests/test_barge_in.py`
- `settings.env`
- `README.md`
- `docs/voice/BARGE_IN_REDESIGN_PLAN.md`
- `docs/awareness-memory/DECISIONS.md`
- `docs/awareness-memory/OPEN_QUESTIONS.md`
- `docs/awareness-memory/IMPLEMENTATION_STATUS.md`

Migrations added: None.

Decisions made:

- ADR-023: the unsafe legacy RMS-plus-ASR barge-in path fails closed through
  both tracked configuration and its code default.
- Synchronized room-audio recording requires a separate explicit operator
  opt-in. It is local-only, visibly announced, background-written through a
  non-blocking bounded queue, capped by duration and PCM bytes, and subject to
  bounded retention of recorder-owned fixture directories.

Assumptions confirmed or changed:

- The legacy detector has no VAD speech probability and no AEC residual signal.
  Metrics report those capabilities as unavailable rather than using mixed
  microphone RMS as a substitute.
- The current `TranscriptResult` contract only optionally exposes
  `confidence`. Segment-level log probability/no-speech evidence remains Phase
  D work and was not added here.
- Fixture collection can remain independent of unsafe interruption, allowing
  `TALOS_BARGE_IN=0` while an operator deliberately records a test session.

Tests run:

1. `python -m unittest tests.test_barge_in tests.test_barge_in_observability tests.test_streaming_speaker`
2. `python -m unittest tests.test_barge_in tests.test_barge_in_observability tests.test_streaming_speaker tests.test_stt_faster_whisper tests.test_sentence_chunker tests.test_voice_benchmarking tests.test_barge_in_agent_integration tests.test_text_server_interrupt`
3. `.venv-main\Scripts\python.exe -m unittest tests.test_barge_in_agent_integration tests.test_text_server_interrupt`
4. `.venv-voice\Scripts\python.exe -m unittest tests.test_barge_in tests.test_barge_in_observability tests.test_streaming_speaker tests.test_stt_faster_whisper tests.test_sentence_chunker tests.test_voice_benchmarking`
5. `py -3.14 -m unittest tests.test_barge_in_agent_integration tests.test_text_server_interrupt`
6. `python -m py_compile talos\voice\agent.py talos\voice\streaming\barge_in.py talos\voice\streaming\barge_in_observability.py tests\test_barge_in.py tests\test_barge_in_observability.py`
7. `git diff --check`
8. Final dependency-light regression:
   `python -m unittest tests.test_barge_in tests.test_barge_in_observability tests.test_streaming_speaker tests.test_stt_faster_whisper tests.test_sentence_chunker tests.test_voice_benchmarking`

Tests passed:

- Command 1: 49/49 passed.
- Command 2: 65 tests passed; the two integration modules failed during import
  and did not run.
- Command 6: passed.
- Command 7: passed (line-ending conversion warnings only).
- Command 8: 65/65 passed.

Tests failed:

- Command 2: `tests.test_barge_in_agent_integration` and
  `tests.test_text_server_interrupt` failed to import because the active
  Inkscape Python did not have `openai`.
- Commands 3 and 4 did not create a Python process because both repository venv
  launchers point at a missing Python 3.12 installation.
- Command 5 found `openai` but both integration modules still failed to import
  because that system Python did not have `python-dotenv`.
- These are dependency/environment failures, not assertion failures. No
  dependency was installed or changed.

Commands not run:

- No live microphone, speaker, endpoint, AEC, model, or soak test.
- No actual room-audio fixture recording.
- No dependency install, Windows AEC probe, or WebRTC binding evaluation.
- No Phase B or later implementation.

Known limitations:

- The legacy heuristic remains structurally unreliable. Phase A contains it but
  does not improve its double-talk discrimination.
- Aggregate measurements are cumulative for the voice-worker process. They do
  not contain transcripts/PCM and use bounded rejection labels.
- Faster-whisper currently provides no confidence in the active backend result,
  so ASR confidence remains truthfully absent until supported.
- Fixture WAV streams use monotonic per-block timestamps and sample offsets for
  synchronization; live device-clock accuracy has not been measured.
- Graceful recorder close is wired into normal voice-worker shutdown. Abrupt
  process termination may leave the newest fixture incomplete.

Security implications:

- Raw room audio is off by default and requires
  `TALOS_BARGE_IN_FIXTURE_RECORDING=1`.
- The recorder prints a visible warning, stores only locally, writes under an
  ignored local data directory by default, enforces duration/byte/session
  bounds, and deletes only prefix-matched directories containing its manifest.
- Privacy-safe barge-in telemetry contains numeric counts/timings/RMS and
  bounded reason labels, never transcript text or PCM.
- Hosted-service behavior is unchanged.

Deployment implications:

- Restart the voice worker to load the fail-closed code/config default and new
  observability.
- Normal deployment should leave both `TALOS_BARGE_IN=0` and
  `TALOS_BARGE_IN_FIXTURE_RECORDING=0`.
- An operator fixture session needs deliberate configuration and should be
  handled as sensitive room audio.

Unresolved questions:

- OQ-H: deployed Windows input/output endpoint IDs, Windows build, driver AEC
  capability, and direct WebRTC APM feasibility remain unknown.
- Owner authorization is required before Phase B, before recording real room
  audio, and before live speaker/microphone testing.

Current repository state:

- Branch: `barge_in_07262026`, tracking `origin/barge_in_07262026`.
- Phase A changes are unstaged.
- Pre-existing untracked `.claude/` was not inspected or modified.

Next permitted task: With separate owner authorization, execute only Phase B:
enumerate/pin the actual Windows endpoints and perform the bounded AEC
feasibility spike. Do not begin Phase C automatically.

Required reading for next session:

- `docs/voice/BARGE_IN_REDESIGN_PLAN.md`
- `docs/awareness-memory/ARCHITECTURAL_INVARIANTS.md`
- `docs/awareness-memory/IMPLEMENTATION_STATUS.md`
- `docs/awareness-memory/OPEN_QUESTIONS.md` (OQ-H)
- `talos/voice/streaming/barge_in_observability.py`
- `talos/voice/streaming/barge_in.py`
- `talos/voice/agent.py`
- this handoff

Explicit stop point: Phase A exit is met. Stop before dependency installation,
AEC backend selection, the real-time audio refactor, live room recording, or any
Phase B+ implementation without separate owner authorization.
