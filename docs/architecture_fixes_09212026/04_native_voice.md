# 04 — Evaluate native voice without discarding the backend

Status: proposed experiment, not an approved replacement.

## Question

Would a local native audio model improve presentness over streaming
STT → text reasoning/tools → TTS? It might improve timing and expression, but
must also handle tool correctness, precise device names/numbers, interruption,
and the available hardware. The current deployment uses local inference and
AWS Polly for speech; OpenAI examples are architecture references only.

## Recommended course

1. Define a speech-independent conversation/backend contract: turns, tool work,
   results, interruption, corrections, and playback acknowledgements.
2. Capture a representative baseline on the current pipeline. Separate endpoint
   detection, STT, prompt processing, model generation, tools, Polly network time,
   synthesis, buffering, and playback where observable.
3. Select one candidate that can actually run on the deployed local hardware.
   Verify its tool interface and licensing/operational fit at selection time;
   do not assume a text model with audio added is full duplex or tool capable.
4. Run an isolated A/B prototype behind the same tool executor and task records.
   Keep the existing local reasoning path available during the comparison.
5. Compare first useful speech, verified action completion, barge-in stopping,
   exact entity/number handling, background-load behavior, and conversational
   quality. Measure acknowledgements separately from useful answers.

Acceptance: naturalness improves without losing grounding, safety, recovery,
or acceptable local resource use. A native model is not automatically faster.
If hosted audio is proposed later, obtain an explicit owner decision about that
deployment change; existing Polly use is not blanket permission for cloud inference.
