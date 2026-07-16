# Phase Launcher Prompts

These files are ready-to-paste launchers for one bounded implementation phase. They deliberately do not repeat the full phase specification.

Before use, the owner should update [`../IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) with the authorized phase and bounded task. Paste only that phase's prompt into a fresh coding-agent session. The agent must read the linked phase brief and references, update status and handoff, report truthfully, and stop. Never chain launcher prompts automatically.

Phase 0 is the required starting point and performs documentation-only discovery. Phase 1 requires completed owner-reviewed Phase 0 unless the owner explicitly records a waiver. Every later prompt verifies the prior phase entry criteria.
