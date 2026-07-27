# Barge-In Voice Interruption: Investigation and Redesign Plan

Status: **design review complete; runtime redesign not yet implemented**

Date: 2026-07-26

Scope: the room-microphone streaming voice path in
`talos/voice/agent.py`, `talos/voice/streaming/barge_in.py`, and its focused
tests. This plan does not change the phone voice path, the agent's cancellation
API, or physical-action authorization.

## Executive conclusion

The downstream cancellation and conversation-record repair are reasonable:
once an interruption is trustworthy, TALOS should stop local playback, cancel
generation, retain only the audible assistant prefix, and dispatch a genuine
follow-up utterance.

The front-end decision is not reliable enough to establish that trust. It tries
to distinguish simultaneous human speech and loudspeaker echo using only
wide-band RMS energy, then uses an unconstrained Whisper transcript as
confirmation. That cannot be tuned into robust full-duplex barge-in. It explains
both reported symptoms:

- Genuine interruptions are missed because a human must substantially overpower
  the loudspeaker echo at the microphone.
- Speaker dynamics, timing gaps, and room noise can trigger ducking. The captured
  audio is then transcribed with VAD disabled, and any non-empty text that does
  not resemble TALOS's own words—including a hallucinated "thank you"—is
  accepted as a user utterance.

Do not make the current energy threshold more permissive. That trades missed
interruptions directly for more false ducking and false commands. Replace the
front end with acoustic echo cancellation (AEC) followed by voice activity
detection (VAD), and keep ASR as a transcription stage rather than proof that
speech happened.

## What was inspected

- Feature commit `f86caee` ("first pass at barge in")
- `talos/voice/streaming/barge_in.py`
- `talos/voice/agent.py`
- `talos/voice/streaming/speaker.py`
- `talos/voice/backends/stt_faster_whisper.py`
- `tests/test_barge_in.py`
- `tests/test_streaming_speaker.py`
- `tests/test_barge_in_agent_integration.py`
- `tests/test_text_server_interrupt.py`
- `settings.env` and the README operating description

The 43 dependency-light detector and speaker tests pass. They prove the state
machine follows its authored rules; they do not prove that those rules separate
real room signals.

## Detailed findings

### 1. RMS cannot solve the double-talk problem

While output is present, the trigger is:

```text
microphone_rms > learned_echo_peak * 2.2
```

The aligned output RMS is used only as a Boolean `speaking` flag. Its amplitude,
waveform, and frequency content are never used to predict or remove echo.

For approximately uncorrelated echo with RMS `E` and human speech with RMS `H`,
the mixed microphone level is approximately `sqrt(E^2 + H^2)`. To exceed
`2.2E`, the human component must be greater than about `1.96E`. A person at
normal volume commonly will not be twice as loud at the microphone as the
nearby speaker. The current behavior—rarely detecting a real interruption—is
therefore expected.

The learned value is also a decaying maximum shared across utterances. A single
loud passage can suppress detection through later, quieter passages, while a
speaker transient above the prior maximum can look like a user.

Relevant code: `talos/voice/streaming/barge_in.py:315-348`.

### 2. Every reply begins with a deliberate deaf interval

The detector recalibrates for eight 64 ms microphone frames every time it is
armed. During those approximately 512 ms, it assumes everything heard while
output is present is echo and refuses to trigger. If a user speaks then, their
voice can also raise the persistent echo peak and make the rest of the
interruption harder to detect.

Relevant code: `talos/voice/streaming/barge_in.py:328-335`.

### 3. The detector is armed when TALOS is not yet speaking

`_arm_playback(session)` runs before the LLM has produced a sentence and before
Polly has returned audio. During that potentially multi-second interval, there
is no aligned output, so the detector uses the low ambient floor. Sustained room
noise can start a capture and duck a session whose first audio has not even
played. Sentence gaps create a similar low-threshold window.

Relevant code: `talos/voice/agent.py:882-918`.

### 4. Short real interruptions are penalized twice

The default trigger consumes three frames (about 192 ms), but those frames are
not credited toward `min_speech_ms`. The user must then remain above the
capture threshold for another 260 ms. A short "stop" can be fully present in
the retained pre-roll yet still be rejected for insufficient counted speech.

After ducking, the capture path can continue using the old, high, pre-duck echo
threshold. Quieter near-end speech is then counted as silence, which compounds
the miss.

Relevant code: `talos/voice/streaming/barge_in.py:346-368` and `:410-421`.

### 5. Energy is being treated as speech

`_speech_ms` counts frames whose RMS exceeds a scaled threshold. It is not a VAD
and has no speech probability. A fan, impact, music, speaker residual, or other
structured noise can satisfy it.

The captured pre-roll intentionally includes loudspeaker echo. The local
faster-whisper backend is configured with `vad_filter=False`, and the
barge-in confirmation does not inspect VAD probability, `no_speech_prob`,
average log probability, or segment duration.

Relevant code:

- `talos/voice/streaming/barge_in.py:351-378`
- `talos/voice/backends/stt_faster_whisper.py:25-40`
- `talos/voice/backends/stt_faster_whisper.py:85-97`

### 6. A plausible ASR string is incorrectly considered evidence of a person

With wake-word enforcement disabled, every non-empty transcript is accepted
unless it textually overlaps TALOS's recorded output. A hallucinated "thank
you" does not overlap most replies, so it is accepted and redispatched as a
user command.

Whisper is a generative ASR model; non-speech hallucination is a known failure
mode. Phrase blocklists are not a sound correction because the same failure can
produce other plausible phrases.

Relevant code: `talos/voice/streaming/barge_in.py:495-532`.

### 7. Confirmation blocks microphone consumption

Whisper inference is called inline from the microphone stream's `read` method.
No new microphone frames are consumed during that inference. This risks input
overflow and loses the beginning of any continued speech or corrected retry.
Real-time capture should enqueue bounded work and continue draining the audio
device.

Relevant code: `talos/voice/agent.py:236-258`.

### 8. The recorded "audible prefix" is sentence-granular and can be false

`session.note_playing(chunk_text)` records an entire synthesized text chunk
immediately before its first PCM block is sent to the sink. If interruption
happens midway through the sentence, memory still says the user heard the
whole sentence.

Relevant code:

- `talos/voice/streaming/speaker.py:98-116`
- `talos/voice/agent.py:908-915`

### 9. The tests encode the optimistic assumptions

The core detector test models echo at RMS 3000 and the user at RMS 12000, an
easy 4× separation. It manually places output at the exact configured delay.
There are no synchronized render/capture recordings containing real double
talk, changing speaker gain, device-clock drift, early interruptions, silent
LLM latency, or Whisper hallucinations on false triggers.

The tests are useful state-machine tests, but the missing signal-level corpus is
why a fully passing suite did not predict room behavior.

## Recommended architecture

```text
TTS PCM ──> render ring ──> AEC reverse/render input ──> speaker
                              │
microphone ──> AEC capture ───┴──> echo-cleaned capture ──> VAD
                                                           │
                                      assistant speaking? ─┤
                                                           v
                                              utterance ring/endpoint
                                                           │
                                                           v
                                              ASR + confidence policy
                                                           │
                                  reject/resume <── decision ──> cancel/dispatch
```

Use a proven acoustic echo canceller, preferably WebRTC Audio Processing
Module/AEC3. WebRTC APM is designed to consume near-end capture and far-end
render streams frame by frame and provides AEC, noise suppression, and voice
statistics. Its documented interface expects approximately 10 ms PCM frames
and says to place it close to the audio hardware.

On the current Windows deployment, first probe whether the selected capture
endpoint exposes Windows communications-mode AEC and allows the TALOS render
endpoint to be selected as its reference. If that is consistently available on
the actual hardware, it may be the lowest-maintenance integration. Otherwise,
use WebRTC APM directly through a maintained, pinned native binding or a small
local audio helper. Do not select a Python wrapper solely because it has a
matching package name; Windows/Python 3.12 support, maintenance, latency, and
licensing must be proven in the spike.

The core policy should be:

1. Keep one long-lived audio engine so the echo filter remains adapted.
2. Use a common device clock when possible. Pin and report exact input/output
   endpoint IDs; detect device changes.
3. Feed the exact PCM sent to the selected render endpoint into the AEC render
   side in fixed frames. Feed microphone PCM into the capture side continuously.
4. Run VAD on the echo-cancelled capture. A short run of high near-end speech
   probability while assistant render is active is a barge-in candidate.
5. Pause or strongly duck playback immediately on that candidate, while capture
   continues. AEC/VAD—not ASR text—establishes that a person spoke.
6. Endpoint the utterance with VAD hysteresis. Transcribe off the real-time
   thread through a bounded queue.
7. Accept only when independent speech evidence and ASR quality/duration rules
   pass. Keep wake-word-required mode as a product policy, not as an echo
   workaround.
8. On rejection, resume playback and emit a reasoned metric. On acceptance,
   reuse the existing local cancel, `/interrupt`, record repair, and ordered
   redispatch path.
9. Track audible text against PCM progress. At minimum, record a chunk only
   after it finishes; preferably use Polly word speech marks to map playback
   milliseconds to text offsets.

## Implementation phases

Each phase is separately reviewable. Do not begin the next automatically.

### Phase A — Containment and measurement

- Treat current barge-in as experimental.
- Until replacement, run with `TALOS_BARGE_IN=0` for zero false commands.
- If the owner explicitly accepts wake-word-only interruption and missed
  barge-ins, `TALOS_BARGE_IN_REQUIRE_WAKE_WORD=1` is a partial containment for
  false redispatch. It does not fix false ducking or create full-duplex audio.
- Add privacy-safe counters and timings:
  `candidate_started`, `candidate_rejected`, `accepted`, VAD probabilities,
  residual/render RMS, capture duration, ASR quality, and pause latency. Do not
  log raw room audio by default.
- Build an explicit opt-in fixture recorder that stores synchronized render and
  capture PCM locally for test-corpus collection. Require a visible operator
  mode and bounded retention because room audio is sensitive.

Exit: the operator can disable unsafe behavior, and test data can be collected
without silently recording the room.

### Phase B — AEC feasibility spike

- Enumerate and pin the actual Windows input/output endpoints.
- Test Windows endpoint AEC support using communications-mode capture and an
  explicit render reference.
- In parallel only if authorized, evaluate a direct WebRTC APM/AEC3 integration
  at 16 kHz mono and 10 ms frames.
- Measure echo return loss enhancement, residual echo during far-end-only
  speech, double-talk preservation, CPU use, and end-to-end delay across speaker
  volumes and after device restart.
- Choose one backend behind a small `DuplexAudioProcessor` interface. Fail
  closed to non-barge-in operation when AEC initialization or endpoint identity
  fails.

Exit: one approach is proven on the deployed Windows host and selected
explicitly. No fallback may silently use the RMS heuristic.

### Phase C — Real-time audio pipeline

- Replace the `SpeechRecognition` stream tap with a continuously drained,
  bounded duplex capture/render pipeline.
- Keep callbacks limited to fixed-frame copy/process/enqueue work; no ASR,
  network call, or file write on the audio thread.
- Maintain bounded render, clean-capture, pre-roll, and utterance rings with
  overflow counters and explicit reset semantics.
- Start the speaking state only when a render frame is actually submitted, not
  while waiting for the LLM or TTS.
- Feed echo-cleaned audio to both idle wake-word capture and speaking-time VAD.

Exit: recorded fixtures and a live loopback prove continuous capture, no
overflow under STT load, and stable AEC alignment.

### Phase D — VAD and decision policy

- Use a speech-probability VAD on AEC output with start/end hysteresis and
  configurable pre-roll.
- Count the triggering speech frames toward minimum speech duration.
- Keep VAD evidence separate from ASR output. Extend `TranscriptResult` with
  bounded segment evidence such as duration, average log probability, and
  no-speech probability where supported.
- Enable faster-whisper's Silero VAD for the barge-in transcription or
  pre-segment with the same VAD. Its defaults are conservative, so tune against
  the fixture corpus instead of copying defaults blindly.
- Remove textual overlap as the primary echo defense. It may remain a secondary
  diagnostic/rejection signal.
- Never special-case "thank you"; reject the non-speech conditions that cause
  arbitrary hallucinated phrases.

Exit: far-end-only and noise fixtures never become user commands, while
double-talk fixtures reliably pause and transcribe.

### Phase E — Accurate interruption bookkeeping

- Track PCM frame progress per TTS chunk.
- Request Polly word/sentence speech marks or implement an equivalent alignment
  contract in the TTS backend.
- On interruption, compute the text prefix at the last emitted PCM time. If
  alignment is unavailable, record only fully played chunks and mark the current
  chunk as partially heard rather than claiming it was complete.
- Preserve the existing ordered flow: local stop, agent cancellation, memory
  correction, then optional follow-up dispatch.

Exit: tests interrupt at multiple points inside one sentence and memory never
contains words scheduled strictly after the cut.

### Phase F — Corpus, acceptance, and rollout

Build synchronized fixtures for:

- far-end output only, including quiet/loud Polly voices and sentence gaps;
- near-end speech only;
- double-talk at several user distances, angles, speaker gains, and phrases;
- early interruption within the first 500 ms;
- short "stop" utterances;
- fans, keyboard, door impacts, music/TV, and household background speech;
- endpoint changes, buffer jitter, capture overflow, and device restart;
- silence/noise clips that previously yielded "thank you" or another ASR text.

Provisional acceptance targets, to be confirmed by the owner:

- at least 95% interruption recall in the defined operating zone;
- no accepted far-end-only/noise commands in an eight-hour soak;
- p95 playback pause no more than 250 ms after near-end speech onset;
- p95 endpoint decision no more than 800 ms after near-end speech ends;
- no audio callback overruns in the soak;
- no regression to idle wake-word latency beyond 50 ms p95;
- truthful memory prefix at tested interruption points.

Roll out behind `TALOS_BARGE_IN_BACKEND=aec` with the old heuristic unavailable
unless explicitly selected for a diagnostic comparison. AEC failure must
disable barge-in, surface degraded status, and leave ordinary wake-word
operation working.

Exit: offline corpus tests, live room scenarios, and an unattended soak meet
the accepted thresholds.

## Tests that should remain

Retain the current downstream tests for:

- local playback cancellation;
- LLM stream cancellation and generator close;
- `/interrupt` validation;
- conversation-record repair;
- stop-only utterances versus follow-up commands;
- stale-session protection and serialized output.

Refactor detector tests around processed capture/VAD events rather than
synthetic RMS separation.

## Dependencies and boundaries

- AEC/VAD must remain deterministic and local. The LLM is not an audio event
  loop or interruption detector.
- No room audio may be sent to a hosted service unless the existing explicit
  remote-STT opt-in is active.
- Barge-in cannot bypass existing action schemas, authorization, confirmation,
  idempotency, acknowledgements, or physical safety interlocks.
- The phone path is already a separate real-time transport and is out of scope.
- AEC capability and selected endpoint identity must be observable; do not claim
  echo cancellation merely because an API call or stream open succeeded.

## Primary references

- [WebRTC Audio Processing Module overview](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/audio_processing/g3doc/audio_processing_module.md)
- [WebRTC AudioProcessing interface](https://webrtc.googlesource.com/src/+/refs/heads/main/api/audio/audio_processing.h)
- [Windows acoustic echo cancellation sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/acousticechocancellation/)
- [Windows audio signal-processing modes](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-signal-processing-modes)
- [faster-whisper VAD documentation](https://github.com/SYSTRAN/faster-whisper#vad-filter)
- [Amazon Polly speech marks](https://docs.aws.amazon.com/polly/latest/dg/speechmarks.html)
- [Careless Whisper: Speech-to-Text Hallucination Harms](https://arxiv.org/abs/2402.08021)

## Explicit stop condition

This investigation stops at the documented recommendation. Runtime AEC
selection, dependency installation, audio-thread refactoring, fixture recording,
and live microphone/speaker tests require owner authorization for the next
bounded task.
