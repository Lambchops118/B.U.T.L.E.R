# Session Handoff — 2026-07-20 — Proactive Presence

```text
Session goal: Let the awareness system speak up on its own — (A) route alerts to a
  spoken, LLM-phrased channel instead of a silent GUI banner; (B) give the backend a
  wall-clock reminder mechanism so "remind me at 7pm" is actually spoken at 7pm.
Current phase: Post-Phase-8 additive work on the completed subsystem. Not a numbered
  phase. Owner authorized both design decisions and the changes directly in chat
  (2026-07-20): (A) always LLM-phrased, assume local Ollama available; (B) backend
  due-time worker (not the legacy APScheduler).

Bounded task completed: Gap A (voice notification channel) + Gap B (due-time reminders).

Files added:
  - talos/awareness/reminders/__init__.py, service.py, worker.py
  - talos/awareness/api/routes/reminders.py
  - talos/awareness/db/migrations/versions/b8f2a1c7d3e9_reminders.py
  - tests/test_awareness_reminders_integration.py
Files modified:
  - talos/text/server.py            (+ POST /speak → enqueues a voice_cmd; LLM phrases + speaks)
  - talos/awareness/notifications/adapters.py  (shared _TextServerAdapter; new VoiceNotificationAdapter;
                                                build_adapters order voice→gui→log)
  - talos/awareness/rules/rules.toml (overflow + source_offline preferred_channel gui→voice)
  - talos/awareness/config.py        (notify_voice_enabled; reminder_interval/interruptibility/channel)
  - talos/awareness/db/models.py     (REMINDER_STATUSES; Reminder model)
  - talos/awareness/api/app.py       (build + run ReminderWorker; include reminders router)
  - talos/awareness/api/routes/health.py    (reminder_worker in /health/components and /metrics)
  - talos/awareness/api/routes/context.py   (set/list/cancel_reminder in /capabilities)
  - talos/mcp_servers/providers/awareness.py (set_reminder/list_reminders/cancel_reminder tools; _post helper)
  - tests/test_awareness_client_and_provider.py (expected tool set + 3 reminder tools)
  - talos/awareness/README.md, docs/awareness-memory/IMPLEMENTATION_STATUS.md (docs)
Migrations added: b8f2a1c7d3e9 (reminders), down_revision 3337c328523b (prior head).

Decisions made:
  - Alerts are spoken via the existing voice_cmd router lane (the same seam the legacy
    morning_report_job uses), so wording is the LLM's; detection/rendering stay
    deterministic in the backend (no Ollama in the awareness process). voice is the
    default preferred channel, with gui+log as automatic fallback for resilience.
  - Reminders live in the awareness backend (durable table + deterministic worker),
    reusing AlertService.raise_attention → identical dedup/quiet-hours/cooldown/audit
    and one shared notification egress for overflow AND reminders.
  - Natural-language time parsing is the LLM's job at set_reminder time; the API
    requires an absolute timezone-aware future due_at and rejects past/naive values.

Assumptions confirmed or changed: preferred_channel is free-form in the rule policy
  (no enum change needed). VoicePayload(command) matches the router's voice_cmd handler.

Tests run / passed:
  - Awareness venv: migration lockstep (1/1, models↔migration in sync incl. reminders);
    reminders integration (2/2); alerts/context/state integration; config/health/rules/
    context/state/actions unit — all pass. py_compile clean on every changed file.
  - Main venv: text-server notify + awareness client/provider + home-automation actions
    (13/13) after updating the provider's expected-tool assertion.
Tests failed: test_awareness_memory_integration.test_memory_lifecycle — PRE-EXISTING,
  Ollama-dependent (embedding attempt_count); reproduces identically on a clean
  `git stash` of this work, so it is not a regression from these changes.
Commands not run: live end-to-end with the real voice worker + TTS + Ollama (no mic/TTS
  in this environment); MQTT broker integration tests (test profile not started).

Known limitations:
  - "Confirmed" on the voice channel means the text server enqueued the spoken alert,
    NOT that a human heard it (documented, consistent with INV-14). Actual speech
    requires the main agent process + Ollama up; if that path is down the outbox falls
    back to the gui banner then the log.
  - The set_reminder tool trusts the LLM to compute the absolute due_at; a wrong offset
    yields a wrong fire time. The API guards only against past/naive timestamps.
  - No recurring reminders (one-shot only) and no "snooze"; add later if wanted.

Security implications: /speak reuses the same text-server auth as /notify (bearer +
  allowed-network). Reminder write routes use require_write_auth (loopback-trusted;
  bearer-gated when TALOS_AWARENESS_API_TOKEN is set) — not fail-closed like physical
  actions, since a reminder performs no physical action.
Deployment implications: run migrations (`python -m talos.awareness migrate`) before
  serving — the reminders table is new. New env vars (all optional, sensible defaults):
  TALOS_AWARENESS_NOTIFY_VOICE_ENABLED, _REMINDER_INTERVAL_SECONDS,
  _REMINDER_INTERRUPTIBILITY, _REMINDER_CHANNEL.
Unresolved questions: none blocking. Optional: whether critical alerts should ALSO
  force a gui banner in parallel (belt-and-suspenders) rather than only on voice failure.

Current repository state: runnable; awareness DB container was started to run tests.
Next permitted task: owner live verification with the real voice worker + TTS + Ollama
  (say "remind me in two minutes to …" and confirm it speaks; trigger an overflow and
  confirm a spoken alert). Then decide on recurring reminders / parallel-banner option.
Required reading for next session: talos/awareness/README.md (voice channel + reminders
  sections), this handoff.
Explicit stop point: implementation and offline tests complete; stopped before any live
  voice/TTS/Ollama run (not available here) and before committing — awaiting owner review.
```
