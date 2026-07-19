# Session Handoff — 2026-07-18 — Tool Scoping & Local-Model Behavior

```text
Session goal: After the Ollama cutover, the local model (mb-core-v1, a Qwen3-14B finetune) mis-selected tools (a "write pygame code" request triggered the kitchen recipe screen) and ignored anti-agentic/followup controls that worked with gpt-4o-mini. Determine whether this is an inherent model limitation and fix what is fixable without making tool use so rigid that inferential behavior ("it's hot in here" -> lower the temperature) is lost.
Current phase: Phases 0-8 complete; owner-authorized post-completion bounded runtime/prompt tuning.
Root-cause finding: Not the model and not the finetune. The effective Qwen3 chat template (from GGUF metadata) is correct, incl. tool formatting. The misfire came from (a) a huge, lopsided tool surface — ~45+ tools passed every turn, 27 of them kitchen_screen_* — with no coding tool, so a 14B grabbed the nearest action tool; and (b) behavioral instructions tuned to gpt-4o-mini's stronger default compliance.
Bounded task completed (owner requested items 1,3,4,5; item 2 "add rigid negative tool guidance" was explicitly declined to preserve inference):
  1. Intent-scoped tool surface: kitchen_screen_* tools are dropped unless the request shows cooking/recipe intent (_is_kitchen_request / _scope_specialized_tools in runtime.py), gated by TALOS_SCOPE_TOOL_SURFACE (default on). Everyday groups (home automation, TV, awareness, memory) stay always-on so inferential requests still reach them. _build_tool_definitions now takes the command; both run_command and run_command_stream pass it.
  3. Interaction-discipline guidance added to personality/overlays/tool_usage.md — pro-inference (act on inferred intent, e.g. too hot -> adjust temperature), single-turn preference, no clarifying-question/"anything else" spam, answer general/knowledge questions directly. Deliberately not the rigid "only exact-match tools" wording the owner rejected.
  4. Thinking heuristic now routes coding/engineering requests to reasoning-on (code/function/script/implement/refactor/traceback/python/pygame/etc. added to talos/agent/thinking.py cues).
  5. Isolation test (stock qwen3:14b vs mb-core-v1, same system prompt + 6-tool surface + same requests, think off and on): IDENTICAL, correct behavior on all cases — pygame -> answered with code (no tool), "it's hot in here" -> set_temperature, recipe -> kitchen tool. Confirms the finetune did not degrade tool-use and the model is fully capable given a sane surface + light guidance; no model swap needed.
Files added: tests/test_tool_scoping.py; docs/awareness-memory/SESSION_HANDOFF_2026-07-18_TOOL_SCOPING_BEHAVIOR.md. Scratchpad-only: compare.py (not in repo).
Files modified: talos/agent/runtime.py (kitchen constants, _has_kitchen_tools/_is_kitchen_request/_scope_specialized_tools, TALOS_SCOPE_TOOL_SURFACE gating, command threaded into _build_tool_definitions + both call sites); talos/agent/thinking.py (coding cues); talos/personality/overlays/tool_usage.md (interaction discipline); tests/test_agent_thinking.py (coding-cue tests); .env.example (TALOS_SCOPE_TOOL_SURFACE doc).
Migrations added: None.
Decisions made: Preserve inference over rigidity — scope tools by intent (structural) and steer behavior with balanced prompt guidance, rather than forbidding tool calls that don't exact-match (owner-declined item 2). TALOS_SCOPE_TOOL_SURFACE defaults on; only the kitchen group is scoped today, mechanism is extensible.
Tests run: .venv-main unittest — tests.test_tool_scoping, tests.test_agent_thinking, tests.test_run_command_stream, tests.test_llm_openai_compat, tests.test_router_voice_fast_routing; py_compile on changed files; live 2-model x 3-case x 2-think comparison via /api/chat.
Tests passed: 44/44 unit; py_compile OK; comparison all-correct for both models.
Tests failed: None.
Commands not run: Full repo suite; live voice; a full-surface (27 kitchen tools) production reproduction of the original misfire (the 6-tool comparison already isolated the cause; deemed sufficient).
Known limitations: Only the kitchen group is intent-scoped; other large future groups can be added to _scope_specialized_tools. Multi-turn cooking followups rely on kitchen-vocabulary terms; a purely pronoun followup ("add two more") mid-recipe could drop kitchen tools for that turn. Scoping keys off the current command only, not conversation state.
Security implications: None. No network/service/env changes this task.
Deployment implications: Restart the streamed text-agent/voice process to load the new code and prompt overlay. No new env required (TALOS_SCOPE_TOOL_SURFACE defaults on).
Unresolved questions: Whether to intent-scope additional groups (filesystem/minecraft) or add conversation-state awareness to kitchen scoping — owner decision.
Next permitted task: Owner live verification; optional extension of scoping to more groups or multi-turn awareness.
Explicit stop point: Stop after items 1/3/4/5, tests, docs, and this handoff. Item 2 intentionally not implemented.
```
