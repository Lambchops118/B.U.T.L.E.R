# 06 — Establish behavioral evaluation and useful CI

Status: proposed; focused regression tests were added for this pass, but the
repository CI workflow was not changed.

## Problem

The inspected CI workflow only compiles InfoPanel, Peripherals, and tests. It
does not compile `talos/` or run the existing behavioral suites. Historical
focused-test totals do not prove current deployed voice quality or tool accuracy.

## Recommended course

1. Establish a reproducible pure-Python test environment. Add application syntax
   checks and a curated unit/contract suite with mocked network/device effects.
2. Keep database/provider integration tests in an explicit isolated lane. Never
   make ordinary CI depend on a live household device or private credentials.
3. Build a small multi-turn evaluation corpus: common commands, stale facts,
   failed tools, malformed calls, unavailable capabilities, corrections, pronouns,
   long histories, interruption, and foreground work during a background task.
4. Record exact model identity, quantization, template, server version, prompt,
   schema set, history, and sampling settings for reproducibility.
5. Measure discovery recall, correct tool/arguments, real outcome, unsupported
   completion claims, recovery, and useful audio latency independently.
6. Run repeated local-model trials. Unit tests verify orchestration contracts;
   live trials establish model behavior. Keep those claims separate.

Use simulated actions for timeout/retry/failure injection. Compare full tools,
curated tools, lexical discovery, and optional semantic discovery on the same
corpus before selecting a catalog strategy. For audio, add representative room
recordings and owner listening assessment once text/tool behavior is sound.

Acceptance: one documented command runs the agreed contract suite; integration
requirements are explicit; latency and success measurements are comparable;
failed evaluation infrastructure is distinguished from an agent task failure.
Do not relax tests or count a fast acknowledgement as completed useful work.
