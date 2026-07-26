# Session Handoff — Voice Physical-Action Dispatch

Session goal:
Diagnose and fix repeated voice claims that a quad-pump action had been initiated even though no relay activated.

Current phase:
Post-Phase-8 bounded hardening/hardware-integration hotfix.

Bounded task completed:
Confirmed from the post-reboot pipeline telemetry and conversation database that the requests “water the monstera” and “turn on the pump for pot two” each completed with zero tool execution while the model generated and persisted an invented initiation claim. Added deterministic routing for unambiguous pump/pot/channel commands through the existing registered `request_device_action` tool. Added a general streamed physical-action guard that withholds an unsupported success claim, retries the model once with a fresh tool requirement, and emits a deterministic truthful failure if no action tool is called. A subsequent live retry proved deterministic tool routing was active, but exposed a second truthfulness defect: the model paraphrased the action API's immediate `approved` (queued) response as “activated.” Explicit pump commands now bypass that model paraphrase and deterministically report the request ID/status with “physical activation has not yet been confirmed”; rejected requests surface their bounded reason and API errors cannot become success prose.

Files added:
`docs/awareness-memory/SESSION_HANDOFF_2026-07-26_VOICE_ACTION_DISPATCH.md`

Files modified:
`talos/agent/runtime.py`; `tests/test_run_command_stream.py`; `docs/awareness-memory/IMPLEMENTATION_STATUS.md`; `docs/awareness-memory/OPEN_QUESTIONS.md`.

Migrations added:
None.

Decisions made:
Clear imperative pump commands with an explicit channel/pot number route deterministically to `run_pump` or `stop_pump` through the registered action boundary. The deterministic parser does not dispatch plant-name-only or otherwise ambiguous requests. It recognizes the deployed speech-to-text variants `too`/`to` for channel 2 and `tree` for channel 3 only when they follow `pump`, `pot`, or `channel`. The awareness action service remains authoritative for authorization, validation, cooldown, MQTT dispatch, acknowledgement, timeout, and audit.

Assumptions confirmed or changed:
The Pico's Wi-Fi/MQTT problem was resolved by correcting the SSID: awareness received canonical state, health, and heartbeats with RSSI -47 and no firmware error. The relay was not implicated by the failed voice tests because the board reported `commands_accepted=0`. Restarting the host does not clear the persisted `voice-worker` conversation, so repeated false model claims remained in prompt history.

Tests run:
`python3 -m py_compile talos/agent/runtime.py tests/test_run_command_stream.py`.

Focused `unittest` suite for `tests.test_run_command_stream`, `tests.test_leaked_tool_calls`, `tests.test_prompting`, `tests.test_current_time_context`, `tests.test_tool_scoping`, and `tests.test_agent_thinking`. WSL could not start the Windows Python executable after the reboot (`UtilBindVsockAnyPort: socket failed 1`), and Linux does not have `openai`; the test command therefore used pure-Python dependencies from `.venv-main/Lib/site-packages` and an import-only `openai` stub. The streamed tests inject their own fake backend and do not call the stubbed module.

Tests passed:
46/46 focused unit tests; both changed files compiled.

Tests failed:
None in the completed focused run. An earlier direct Linux run could not collect tests because `openai` was absent; this was an environment/import failure, not a test assertion failure.

Commands not run:
The full repository suite and a live physical pump command were not run. Windows process/port inspection could not be repeated after WSL interop failed.

Known limitations:
The running main/voice process must be restarted again to load the shortened deterministic action-result wording; request UUIDs remain in the database audit but are no longer spoken. Plant-name-only requests still require the model or stored entity context to select a channel. The Pico is online and publishing recurring heartbeats from a stable boot. Database evidence for the latest channel 3 and channel 1 runs shows command receipt, `relay_N=true`, an approximately eight-second run, `relay_N=false`, and a positive execution acknowledgement; device health reports 6 accepted and 0 rejected commands. That state is firmware-commanded state, not electrical relay feedback. The remaining no-click failure therefore requires direct GP0-GP3 voltage, polarity, power, common-ground, logic-level, and wiring bench checks. Channel-to-physical-pot wiring remains unverified.

Security implications:
No raw MQTT or GPIO bypass was added. Deterministic routing calls the existing registered action tool, preserving API authorization, schema validation, cooldown, idempotency, acknowledgement, timeout, and audit boundaries. Ambiguous requests do not dispatch.

Deployment implications:
Stop and restart TALOS from the launcher. Confirm awareness is healthy on port 8600 before testing. Leave the Pico running `main.py` without interrupting it from Thonny. Issue one explicit command with a channel number and observe the action record, acknowledgement, and relay.

Unresolved questions:
OQ-B, OQ-D, OQ-E, and OQ-F remain open. OQ-G is resolved.

Current repository state:
The worktree already contained extensive owner changes and CRLF line-ending differences. They were preserved. This hotfix modifies only the files listed above.

Next permitted task:
Owner-executed live verification of one explicit pump channel, followed by the quad-pump plan's bench and physical acceptance checks.

Required reading for next session:
`IMPLEMENTATION_STATUS.md`; this handoff; `SESSION_HANDOFF_2026-07-26_WINDOWS_MQTT_HOTFIX.md`; `Peripherals/quad_pump/plan.md`.

Explicit stop point:
Stop after voice dispatch truthfulness/routing and its focused tests. Do not change broker-wide security, canonical legacy cutover, fuse policy, or other peripheral firmware without separate authorization.
