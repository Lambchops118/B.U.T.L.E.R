# Session handoff — announcement routing repair

Session goal:
  Fix the owner's live arrival tests speaking the background-work acknowledgement.
Current phase:
  Bounded post-Phase-9 defect repair, authorized by the owner's report.
Bounded task completed:
  /speak now creates a typed announcement, not a voice command. Router sends
  supplied text to its existing voice-worker helper and GUI, without classifying,
  invoking an agent/tools, creating a job, or emitting human activity signals.
Files added:
  This handoff.
Files modified:
  talos/messages.py; talos/router.py; talos/text/server.py;
  talos/awareness/notifications/adapters.py; tests/test_text_server_notify.py;
  awareness README, implementation status, decisions, and open questions.
Migrations added:
  None.
Decisions made:
  ADR-038. Typed system-output routing fixes classification and protects sourced
  briefing text from a second model rewrite. Existing voice-worker API is reused.
Assumptions confirmed or changed:
  The reported phrase exactly matches router.BACKGROUND_ACK. /speak's previous
  instruction wrapper entered the ordinary command classifier and job branch.
  System output also incorrectly emitted presence/interaction facts there.
Tests run:
  Bundled Python with awareness packages and site.addsitedir for main packages:
  unittest modules tests.test_text_server_notify,
  tests.test_router_voice_fast_routing, tests.test_router_phone_routing,
  tests.test_router_job_status. py_compile on five touched Python files;
  git diff --check.
Tests passed:
  18/18. New regression exercises authenticated HTTP /speak through router with
  speech mocked, asserting exact text and zero classifier/model/job/presence work.
  Also verifies authorization, required title, and empty-body title fallback.
Tests failed:
  None.
Commands not run:
  Full suite, live speech, production process restart, or voice latency benchmark.
Known limitations:
  Text-server HTTP 200 still confirms enqueue, not audible playback. The existing
  speech helper remains best-effort. Source text may be technical; this repair
  does not add greeting generation or promise verbatim "welcome home" output.
Security implications:
  Existing /speak authorization is preserved. Announcements cannot run tools or
  physical actions. No new external integration or speech-worker endpoint.
Deployment implications:
  Restart main agent/text server to load the changed message type and router.
  Repeat absent→present injection afterwards; previous committed receipts remain.
Unresolved questions:
  OQ-O live playback/latency verification remains open; routing cause is fixed.
Current repository state:
  Uncommitted changes. Existing Phase 9 work, owner settings, and live logs preserved.
Next permitted task:
  Owner-visible retest after main-agent restart.
Required reading for next session:
  This handoff; ADR-038; /speak handler; router announcement branch; regression test.
Explicit stop point:
  Stop at this repair. No further voice pipeline or unrelated phase changes.
