# Session Handoff — Barge-In Phases B-F

Session goal: Implement all remaining phases of `docs/voice/BARGE_IN_REDESIGN_PLAN.md`.

Current phase: Phases A-F implementation complete; owner-visible Phase F room acceptance pending.

Bounded task completed: Selected and proved Windows communications AEC; added pinned endpoint checks, bounded continuous duplex capture, clean idle recognition, stateful Silero VAD with hysteresis/pre-roll, bounded off-audio-thread ASR, segment evidence gates, safe PCM/chunk bookkeeping, an offline fixture acceptance runner, rollout controls, and documentation.

Files added: Windows AEC, duplex and VAD modules; AEC probe; acceptance runner; fixture manifest example; focused tests; and this handoff.

Files modified: Voice agent, barge-in detector/observability, streaming speaker, STT contract/backend, voice requirements, settings, README, redesign plan, decisions, open questions, and implementation status.

Migrations added: None.

Decisions made: ADR-024 selects pinned Windows communications AEC; the RMS path is diagnostic-only and never fallback; production stays disabled pending live acceptance.

Assumptions confirmed or changed: `.venv-voice` is usable outside the restricted sandbox. Windows exposes active communications AEC on the deployed Yeti/BenQ pair. The driver uses a verified system-default render reference rather than exposing explicit reference configuration.

Tests run: 84 focused tests; Python syntax compilation; `git diff --check`; a bounded in-memory AEC speaker/mic probe; a sub-second live duplex startup; an end-to-end AEC `AudioSource` calibration/background-listener startup; one repository-wide unittest discovery run.

Tests passed: All 84 focused tests. AEC probe: 45.696 dB ERLE, correlation reduced by 0.053797, zero callback errors. Duplex startup: 71 frames, no queue drops, no processor error. The full AEC-backed recognizer source calibrated and started/stopped its background listener with no drops or processor error.

Tests failed: The repository-wide run completed 381 tests with 2 unrelated failures and 12 unrelated/environmental errors (20 skips): missing awareness dependencies, Windows symlink privilege, pre-existing phone constructor/database-cleanup failures, and MCP assertions/import layout.

Commands not run: No owner-room double-talk corpus, device-restart matrix, multi-volume matrix, latency measurement, or eight-hour soak. Production barge-in was not enabled.

Known limitations: Without Polly word speech marks, an interrupted current chunk is deliberately under-claimed as partially heard; completed earlier chunks remain exact. Live room acceptance remains unknown.

Security implications: Raw room PCM remains off by default, explicit, bounded, local-only, and uncommitted. AEC/VAD/ASR are local. No new action authority is introduced.

Deployment implications: Install pinned PyWinRT packages from `requirements-voice-py312.txt`. The full MMDevice IDs are host-specific. Endpoint mismatch disables barge-in and preserves ordinary wake capture.

Unresolved questions: OQ-I, the owner-visible corpus and soak.

Current repository state: Changes are unstaged. Pre-existing untracked `.claude/` was not touched.

Next permitted task: Run the explicit room fixture matrix and soak, evaluate with `python -m talos.voice.diagnostics.barge_in_acceptance <manifest.json>`, tune against evidence if needed, then authorize `TALOS_BARGE_IN=1`.

Required reading for next session: The redesign plan, this handoff, `duplex.py`, `vad.py`, `windows_audio.py`, and `barge_in_acceptance.py`.

Explicit stop point: Do not enable production barge-in or silently collect room PCM before owner-visible acceptance.
