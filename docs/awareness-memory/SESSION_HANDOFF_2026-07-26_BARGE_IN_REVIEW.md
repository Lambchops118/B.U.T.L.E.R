Session goal: Investigate the unreliable barge-in voice interruption feature at
both code and architectural levels; fix it only if the design is sound,
otherwise produce a replacement plan.

Current phase: Post-Phase-8 bounded voice review. Awareness/memory phases 0-8
remain complete and unchanged.

Bounded task completed: Traced microphone capture, render tracking, energy gate,
ducking, endpointing, faster-whisper transcription, interruption
classification, local playback cancellation, agent cancellation, conversation
repair, and follow-up redispatch. Determined that the front-end RMS-plus-ASR
design cannot robustly distinguish double-talk from echo. Documented an
AEC-first redesign with phased implementation and acceptance criteria. Added a
README warning. Runtime behavior was intentionally not patched.

Files added:

- `docs/voice/BARGE_IN_REDESIGN_PLAN.md`
- `docs/awareness-memory/SESSION_HANDOFF_2026-07-26_BARGE_IN_REVIEW.md`

Files modified:

- `README.md`
- `docs/awareness-memory/IMPLEMENTATION_STATUS.md`
- `docs/awareness-memory/OPEN_QUESTIONS.md`

Migrations added: None.

Decisions made: No owner-level architecture decision was recorded. The review
recommends WebRTC APM/AEC3 or a proven Windows endpoint AEC path, with AEC output
feeding VAD before ASR. Exact backend selection remains OQ-H pending a
deployment-host spike.

Assumptions confirmed or changed:

- Confirmed by repository: the voice worker is launched on Windows, uses
  separate PyAudio input/output streams, runs faster-whisper locally by default,
  and has no AEC dependency.
- Changed: passing state-machine unit tests do not establish room-signal
  correctness. The tests assume exact delay alignment and a human signal four
  times the simulated echo.
- Changed: ASR output cannot be treated as independent proof of voice activity,
  especially when VAD is disabled.

Tests run:

- `python3 -m unittest tests.test_barge_in tests.test_streaming_speaker`
- Attempted the four-suite run with `.venv-voice/Scripts/python.exe`.
- Checked the new documentation for trailing whitespace and verified its local
  file references.
- Ran focused `git diff --check`; the repository's pre-existing CRLF-normalized
  modified files were reported wholesale as trailing whitespace, so that output
  could not serve as a clean validation of the new hunks.

Tests passed: 43 tests passed in the dependency-light detector and streaming
speaker suites.

Tests failed: None in the run that started.

Documentation checks: New documentation has no trailing whitespace and its
referenced local files exist. Focused semantic diffs were inspected with
`--ignore-space-at-eol`.

Commands not run:

- The Windows-venv four-suite run did not start because WSL-to-Windows process
  interop failed with `UtilBindVsockAnyPort: socket failed 1`.
- No live microphone/speaker test, raw-audio recording, Whisper model inference,
  Windows AEC capability probe, full repository suite, formatter, or linter was
  run.

Known limitations:

- Current barge-in remains enabled by the tracked `settings.env` and remains
  unreliable until the operator disables it or the redesign is implemented.
- Wake-word-required mode only contains false command acceptance; it does not
  prevent false ducking or solve double-talk detection.
- The exact deployed input/output endpoints, Windows build, driver AEC support,
  room response, and native WebRTC binding choice were not available from the
  repository.
- Current "audible prefix" bookkeeping marks a whole sentence at its first PCM
  block, not the exact text heard.

Security implications: False ASR text can currently enter the conversation as a
user command. Existing action authorization and safety boundaries still apply,
but this is not an acceptable authentication signal. Optional synchronized
audio fixture recording in the plan is explicitly opt-in, local, visible, and
retention-bounded because room audio is sensitive.

Deployment implications: Safe interim posture is
`TALOS_BARGE_IN=0`. A production redesign requires a Windows endpoint/AEC
capability spike on the deployed host. AEC failure must degrade to ordinary
wake-word operation without barge-in, never silently back to the RMS heuristic.

Unresolved questions: OQ-H records the AEC backend and endpoint evidence needed
before implementation. Owner acceptance targets and permission to record a
local synchronized test corpus also remain open.

Current repository state: The worktree contained broad pre-existing
modifications before this task. Only the five files listed above were added or
modified for this review; unrelated changes were preserved.

Next permitted task: With owner authorization, execute Phase A and then the
Phase B Windows AEC feasibility spike in
`docs/voice/BARGE_IN_REDESIGN_PLAN.md`. Separately, the earlier quad-pump GPIO
deployment and physical acceptance remain pending.

Required reading for next session:

- `docs/voice/BARGE_IN_REDESIGN_PLAN.md`
- `talos/voice/streaming/barge_in.py`
- `talos/voice/agent.py`
- `tests/test_barge_in.py`
- `docs/awareness-memory/ARCHITECTURAL_INVARIANTS.md`

Explicit stop point: Stop after this design review and documentation. Do not
install an AEC dependency, refactor the audio path, record room audio, run live
speaker tests, or begin another awareness phase without separate owner
authorization.
