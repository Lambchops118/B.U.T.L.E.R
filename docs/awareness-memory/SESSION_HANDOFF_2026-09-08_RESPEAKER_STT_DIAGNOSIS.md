# Session Handoff — ReSpeaker XVF3800 STT Diagnosis

Session goal: Investigate the severe STT accuracy regression after replacing
the Blue Yeti with a Seeed Studio ReSpeaker XVF3800 USB 4-mic array, and identify
whether the cause is in TALOS or the device.

Current phase: Bounded post-Phase-F voice diagnosis; no runtime repair was
authorized or performed.

Bounded task completed: Traced the configured and live Windows capture paths,
enumerated the exact PortAudio devices used by the voice environment, compared
fresh voice telemetry before and after the fallback, inspected the TALOS
capture/STT code, checked current official ReSpeaker documentation and firmware
history, and queried the connected XVF3800 through its official USB control
protocol in read-only mode.

Files added: This handoff.

Files modified: `IMPLEMENTATION_STATUS.md` and `OPEN_QUESTIONS.md` only.

Migrations added: None.

Decisions made: None. OQ-P records the owner-visible acceptance and capture
architecture choice required before a repair.

Assumptions confirmed or changed:

- Windows and PortAudio currently select the ReSpeaker as default capture. The
  exact default capture endpoint is `...{5dda5d51-9956-4efa-8d6b-377422f40173}`.
- `settings.env` still pins the old Yeti capture endpoint
  `...{1783577d-4c7a-4b7b-b2eb-51c2dfbc9087}`. `_start_aec_duplex` requires exact
  equality, so current startup falls back to `sr.Microphone()` and disables the
  idle VAD lane even though both idle-VAD flags are set.
- Current telemetry agrees with the code path: the active post-switch worker
  reports no AEC residual capability. A nearby earlier session reported AEC
  residuals, making the boundary visible without relying only on configuration.
- SpeechRecognition 3.14.3 opens exactly one channel. Its default ReSpeaker
  MME endpoint advertises two input channels at 44.1 kHz; the device also exposes
  two-channel WASAPI at 48 kHz and two-channel WDM-KS at 16 kHz. TALOS converts
  the resulting mono stream to 16 kHz for faster-whisper but does not explicitly
  select the device's ASR channel.
- Official XVF3800 documentation says left and right have different semantics;
  the right channel is the ASR output of the auto-selected beam. Live USB reads
  returned firmware 2.0.6, 16-bit USB, left route `(8,0)`, right route `(7,3)`,
  `AEC_ASROUTONOFF=1`, `SHF_BYPASS=0`, `AUDIO_MGR_MIC_GAIN=90`, AGC enabled,
  `PP_AGCMAXGAIN=64`, 0.9-second normal AGC timing, non-speech attenuation off,
  fixed-beam gating off, and expected stock suppression/output gains. This is
  internally consistent and does not indicate a broken or accidentally raw
  device configuration.
- The current fallback retains device-specific assumptions from the Yeti:
  recognizer energy threshold 500, post-capture RMS gate 300, and generic mono
  capture. Fresh post-switch telemetry showed many low-level, tightly clustered
  clips: 32 STT completions but only two full wake-word pipeline completions at
  the time of the bounded snapshot, with half of the clip-average RMS values
  below 500. Non-wake transcripts are deliberately not logged, so this is not a
  formal word-error-rate measurement, but it directly corroborates the reported
  wake-recognition collapse.
- The device reported `AEC_AECCONVERGED=0` during a quiet read. TALOS renders to
  the BenQ endpoint, not through the ReSpeaker playback endpoint, so the
  ReSpeaker cannot be assumed to have the far-end reference needed for hardware
  echo cancellation. This affects duplex/barge-in validation more than quiet
  idle STT and is not evidence of defective microphones.
- Firmware 2.0.6 is behind current 2.1.0, but the official intervening changelog
  does not identify an ASR-quality correction applicable to this symptom. A
  firmware upgrade is therefore not justified as the first response.

Tests run: No unit tests; runtime code was unchanged. Read-only checks included
Windows PnP/MMDevice enumeration, PortAudio and SpeechRecognition enumeration in
`.venv-voice`, non-recording PortAudio format-support checks, TALOS's own WinRT
default-endpoint query, bounded JSONL/CSV telemetry inspection, and official USB
control reads for version, routing, bit-depth, AEC, beam/ASR, AGC, and
noise-processing parameters. The current MME endpoint accepts explicit 16 kHz
mono or stereo, and the WDM-KS endpoint accepts native 16 kHz stereo; the
44.1 kHz generic default is therefore not forced by the device.

Tests passed: Every read-only repository/Windows query completed except the
first sandboxed PnP/CIM attempt, which was rerun with approved host access. All
29 requested ReSpeaker USB controls returned successfully.

Tests failed: The initial sandboxed PnP/CIM query returned access denied, and an
initial sandboxed `.venv-voice` process launch could not reach its base Python;
both succeeded after the normal host-access approval. A first WinRT query used
the wrong function name and raised `ImportError`; the corrected repository
function returned the exact endpoints.

Commands not run: No microphone recording, channel-separated WAV capture,
Whisper A/B transcription, firmware flash, device parameter write, configuration
change, process restart, Windows AEC probe, full suite, or soak test.

Known limitations: Windows/PortAudio's precise stereo-to-mono mixing behavior
was not established from room audio. Whether it selects left or combines both
channels, the current path does not intentionally consume the documented right
ASR channel. The telemetry lacks rejected transcript text by design, so the
snapshot measures pipeline acceptance rather than WER against ground truth.

Security implications: None. Audio remained local and no PCM was persisted.
The official control tool and Python dependencies were downloaded only to the
user temporary directory for read-only USB queries.

Deployment implications: The deployed voice worker is presently on the generic
SpeechRecognition fallback, not the pinned AEC/idle-VAD path described by the
tracked settings. Merely changing Windows's default microphone is insufficient
because TALOS separately pins full MMDevice identities.

Unresolved questions: OQ-P. A visible, consented channel-separated A/B corpus is
needed to choose and verify the capture contract. Hardware AEC versus Windows
communications AEC also needs an explicit design choice to avoid accidental
double processing and to ensure the far-end reference actually exists.

Current repository state: Pre-existing/user-generated changes remain in
`talos/logs/voice_benchmarks.csv`, `.claude/`, and several 2026-09-08 telemetry
JSONL files. This session did not modify them. Only the three documentation
files named above belong to this diagnosis.

Next permitted task: With owner authorization, implement a narrowly scoped
XVF3800 capture adapter/configuration that pins the current endpoint and selects
the documented ASR channel, derives or configures device-appropriate segmentation
thresholds, then run a visible A/B wake/command corpus before enabling it as the
production contract. Validate quiet idle speech separately from duplex playback.

Required reading for next session: This handoff, `IMPLEMENTATION_STATUS.md`,
`OPEN_QUESTIONS.md` OQ-P, `SESSION_HANDOFF_2026-08-09_WAKE_LATENCY_ACCURACY.md`,
`docs/voice/BARGE_IN_REDESIGN_PLAN.md`, `talos/voice/agent.py`,
`talos/voice/streaming/windows_audio.py`, `talos/voice/streaming/duplex.py`,
`talos/voice/backends/stt_faster_whisper.py`, and `settings.env`.

Explicit stop point: Do not change the endpoint pin, select/remap channels,
tune thresholds, enable or disable AEC/VAD, flash firmware, restart the worker,
or record room audio until the owner authorizes the repair and visible corpus.
