# Session handoff — announcement recall

Session goal: Owner requested remembering proactive output in later voice turns.
Current phase: Bounded Phase 9 follow-up, explicitly authorized.
Bounded task completed: Persist exact rendered wording in existing delivery
  receipts; expose recent accepted voice output in situation context and saved
  briefing text through the existing history tool. No fabricated legacy backfill.
Files added: This handoff.
Files modified: briefing/service.py, briefing/worker.py, context/broker.py,
  notifications/handler.py, mcp_servers/providers/awareness.py,
  test_awareness_briefing_delivery.py, test_awareness_alerts_integration.py,
  awareness README, implementation status, decisions and open questions.
Migrations added: None; existing receipt JSON metadata is used.
Decisions made: ADR-040. Output is historical evidence of an attempted/accepted
  announcement, not a fact about a real arrival or proof of playback.
Assumptions confirmed or changed: Receipts previously lacked rendered wording;
  situation context did not retrieve them. Ordinary awareness alerts/reminders
  share the new recording mechanism. Other direct speech callers are out of scope.
Tests run: tests.test_awareness_briefing_delivery,
  tests.test_awareness_context_unit, tests.test_awareness_context_integration,
  tests.test_awareness_alerts_integration using bundled Python and venv packages.
Tests passed: Final 16/16; scratch PostgreSQL, no live notification calls.
  Seven changed Python files compile; git diff --check passed.
Tests failed: First run had two SQL row-label errors; fixed with explicit label.
  Subsequent run and final run after additional assertions passed.
Commands not run: Full suite; live speech/recall; production restart; latency test.
Known limitations: Latest three voice receipts in 24 hours enter budgeted context;
  long excerpts truncate at 800 characters and are labeled. Full briefing wording
  remains tool-accessible within existing history/retention limits. Legacy receipt
  wording is unknown. Baseline p50 1238 ms / p95 2541 ms was not remeasured.
Security implications: No model calls, physical actions, or external services.
  Quoted announcement data is explicitly distinguished from instructions/facts.
Deployment implications: Restart awareness backend and MCP provider/main agent.
  Use a fresh briefing to test recall. No migration or changes to trigger behavior.
Unresolved questions: OQ-O live acceptance remains open.
Current repository state: Existing uncommitted work, settings and live logs preserved.
Next permitted task: Owner live retest of new briefing and follow-up question.
Required reading for next session: This handoff, ADR-040, broker announcement query.
Explicit stop point: Stop at announcement recall; no later phase or integration.
