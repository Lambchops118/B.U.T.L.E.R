# Session Handoff — Selectable Yeti/ReSpeaker Capture

Session goal: Repair the severe STT regression after switching from a Blue Yeti
to a Seeed Studio ReSpeaker XVF3800, and add an explicit launcher choice for
either microphone.

Current phase: Bounded post-Phase-F voice repair authorized by the owner after
the 2026-09-08 device/code diagnosis.

Bounded task completed: Added shared microphone profiles, deterministic named
PortAudio capture, stereo PCM channel selection, launcher GUI and headless
selection, profile-specific endpoint/threshold behavior, regression tests, and
operator documentation. Set the tracked and current machine-local launcher
selection to ReSpeaker.

Files added: `talos/voice/microphone_profiles.py`,
`talos/voice/streaming/portaudio_input.py`,
`tests/test_microphone_profiles.py`,
`tests/test_launcher_microphone_profiles.py`, and this handoff.

Files modified: `talos/voice/agent.py`, `talos/launcher/config.py`,
`talos/launcher/core.py`, `talos/launcher/gui.py`,
`talos/launcher/__main__.py`, `settings.env`, `README.md`,
`IMPLEMENTATION_STATUS.md`, `DECISIONS.md`, and `OPEN_QUESTIONS.md`. The ignored
machine-local `launcher.config.json` now persists `microphone_profile` as
`respeaker`.

Migrations added: None.

Decisions made: ADR-041 selects microphone-specific capture contracts. The
ReSpeaker uses 16 kHz stereo USB channel 2 and calibrated SpeechRecognition
segmentation. Its barge-in and experimental idle VAD are disabled until a real
far-end/AEC topology and owner-visible corpus pass. The Yeti keeps its existing
Windows communications-AEC contract and fixed threshold.

Assumptions confirmed or changed:

- Merely changing the Windows default capture device is insufficient. The voice
  worker now opens the profile's named PortAudio input for ordinary capture.
- The connected ReSpeaker resolves as MME input index 1, name
  `Echo Cancelling Speakerphone (r`, two input channels, default 44.1 kHz, and
  accepts the explicit 16 kHz stereo format used by the adapter.
- SpeechRecognition consumes the mono channel returned by the adapter; channel
  extraction occurs before ambient calibration, utterance segmentation, RMS
  gating, and faster-whisper.
- ReSpeaker's launcher profile overwrites Yeti-era segmentation settings with
  `TALOS_RECOGNIZER_ENERGY_THRESHOLD=auto` and disables barge-in/idle VAD for
  that worker run. Yeti overwrites the threshold with `500` and otherwise
  preserves the existing rollout flags.
- The Yeti can still be selected while Windows defaults to ReSpeaker: ordinary
  capture uses the named Yeti. Its AEC path intentionally remains fail-closed
  unless the selected pinned endpoint also matches the active Windows defaults.

Tests run: `.venv-voice` unittest discovery for
`test_microphone_profiles.py`, `test_launcher_microphone_profiles.py`,
`test_windows_audio.py`, `test_duplex_audio.py`, `test_barge_in_vad.py`, and
`test_stt_faster_whisper.py`; `py_compile` for all touched Python files;
`git diff --check`; launcher `--help`; saved-config environment inspection; and
a non-recording real PortAudio device resolution and stream open/close check.

Tests passed: 37 distinct focused unit tests, with the five launcher tests also
re-executed successfully in `.venv-main`; Python compilation; whitespace check;
launcher help exposed `{respeaker,yeti}`; saved config produced the ReSpeaker
safe environment; live device selection resolved the expected two-channel MME
endpoint, opened it at 16 kHz stereo with the channel 2 contract, then closed it
without reading PCM.

Tests failed: The first combined unittest command used dotted `tests.*` imports,
but this repository's `tests` directory is a namespace without `__init__.py`;
six loader errors occurred before any test body ran. Re-running those same files
through unittest discovery passed. An initial read-only diagnostic command used
a nonexistent profile convenience method and raised `AttributeError`; the
runtime code did not call that method, and the corrected check passed.

Commands not run: Full repository suite; live microphone PCM read or recording;
faster-whisper transcription on room speech; owner-visible Yeti/ReSpeaker phrase
corpus; playback/double-talk test; eight-hour soak; process restart; firmware
flash or USB parameter write.

Known limitations: Production recall and WER are not yet measured on the new
path. The existing post-capture RMS gate remains 300. ReSpeaker barge-in and
experimental idle VAD are unavailable by design until their independent
acceptance requirements pass. If the USB friendly name changes, its configurable
name fragment must be updated.

Security implications: Capture remains local. No PCM or transcript content was
added to logs or persisted by this repair. Profile selection only changes the
voice child process environment.

Deployment implications: Restart the voice worker, or stop and restart the
launcher stack, to load the selected profile. Use the GUI **Room microphone**
dropdown or headless `--microphone respeaker|yeti`. No firmware upgrade is
required for this repair.

Unresolved questions: OQ-P's owner-visible accuracy comparison. Any future
ReSpeaker barge-in work also needs a deliberate far-end reference and must not
assume the BenQ playback is visible to XVF3800 hardware AEC.

Current repository state: The requested changes are unstaged. Pre-existing/user
runtime changes in `talos/logs/voice_benchmarks.csv`, `.claude/`, and the
2026-09-08 pipeline telemetry JSONL files were not modified by this repair.

Next permitted task: Owner-visible operational acceptance: restart with the
ReSpeaker selected and compare a fixed wake/command phrase set against the Yeti,
without silently saving room audio. Tune the remaining RMS gate only from that
evidence if needed.

Required reading for next session: This handoff, the preceding ReSpeaker
diagnosis handoff, ADR-041, OQ-P, `talos/voice/microphone_profiles.py`,
`talos/voice/streaming/portaudio_input.py`, `talos/voice/agent.py`, and
`docs/voice/BARGE_IN_REDESIGN_PLAN.md`.

Explicit stop point: Stop at the selectable capture repair. Do not claim
production accuracy, enable ReSpeaker AEC/barge-in or experimental idle VAD,
record room PCM, tune thresholds without a visible corpus, flash firmware, or
start another awareness-memory phase without owner authorization.
