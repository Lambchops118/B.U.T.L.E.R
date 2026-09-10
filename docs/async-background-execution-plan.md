# Butler Async Background Execution Plan

## Purpose

This document describes how to evolve Butler from a synchronous request/response agent into a hybrid system with:

- a fast conversational lane that can acknowledge requests immediately
- a background execution lane for long-running MCP work
- persistent sessions across voice and text clients
- support for many MCP servers, not just KiCad

This is intended to be usable later by a developer or coding agent as an implementation guide.

## Core User Experience Goal

The system should support interactions like:

1. User says: "I need you to design a basic RLC circuit."
2. Butler replies immediately: "I can do that. Give me a minute."
3. Butler starts a background job that may use MCP tools, planning, retries, and long-running CAD actions.
4. Butler later delivers the result through the active client, linked session, or a status/query API.

The main agent process should remain responsive while background work is in progress.

## Why This Change Is Needed

The current Butler flow is synchronous:

- [butler/router.py](../butler/router.py) pulls a message from the central queue
- it calls `butler.agent.runtime.run_command(...)`
- it waits for the full LLM and MCP workflow to finish
- only then does it reply to the caller

This has several problems for the target architecture:

- one slow request can block unrelated interactions
- expensive MCP startup is paid on the request path
- the system cannot acknowledge work immediately and continue in the background
- the architecture will get worse as more MCP servers are added

The text server is also built around this blocking assumption:

- [butler/text/server.py](../butler/text/server.py) sends a request into `central_queue`
- then blocks on `reply_queue.get(...)` until the full request completes

## Current Constraints

The current codebase has these useful properties:

- the main agent process is already persistent
- there is already a central queue
- there is already session-aware text interaction
- `butler.agent.runtime` is already a reusable execution boundary

The current codebase also has these limitations:

- no concept of a background job
- no concept of task status beyond a single synchronous response
- no async notification channel for job completion
- no separation between lightweight chat requests and heavyweight execution requests

## Target Architecture

Butler should be split conceptually into two lanes.

### 1. Conversational Lane

Responsibilities:

- receive voice and text input
- classify the request as quick or backgroundable
- respond immediately when background execution is appropriate
- answer status questions like "how is that going?"
- ask follow-up questions if the job needs more information

Latency target:

- sub-second to a few seconds

### 2. Background Execution Lane

Responsibilities:

- run long-lived MCP-heavy workflows
- manage job state, progress, retries, and results
- isolate slow tools and tool startup from interactive chat
- support multiple workers and specialized worker pools later

Latency target:

- not user-facing
- correctness and robustness matter more than immediate completion

## Recommended High-Level Design

Implement a `JobManager` and one or more background workers, while preserving the current synchronous path for lightweight requests.

At a high level:

1. Client sends a message.
2. Butler classifies it as either:
   - foreground request
   - background-capable request
3. If foreground:
   - execute using the current `butler.agent.runtime.run_command(...)` style path
4. If background:
   - create a durable job record
   - return an immediate acknowledgment
   - execute the job in a background worker
   - store progress and final results
   - notify the session or make results queryable

## Key Design Principle

Do not make the main interactive request path depend on the startup or tool surface of every MCP server.

This is especially important for:

- KiCad
- future CAD or robotics MCPs
- remote MCPs with network latency
- MCP servers with heavy import costs or discovery costs

## Main New Components

### JobManager

Create a new module, for example:

- `butler/jobs.py`

Responsibilities:

- create jobs
- assign IDs
- persist job state
- enqueue jobs
- update progress
- store results and failures
- provide lookup by job ID and session ID

Suggested job states:

- `queued`
- `starting`
- `running`
- `waiting_for_input`
- `completed`
- `failed`
- `cancelled`

Suggested job fields:

- `job_id`
- `session_id`
- `request_text`
- `status`
- `created_at`
- `updated_at`
- `started_at`
- `finished_at`
- `priority`
- `job_type`
- `assigned_worker`
- `progress_message`
- `result_summary`
- `result_payload`
- `error_message`
- `conversation_snapshot`
- `requires_user_input`
- `parent_message_id` or similar trace metadata

### BackgroundWorker

Create a worker module, for example:

- `butler/background_worker.py`

Responsibilities:

- claim jobs from `JobManager`
- run the full LLM and MCP workflow
- report progress
- handle failures and retries
- publish final results

The first version can use Python threads. A later version can move certain workloads to separate processes if needed.

### Request Classifier

Create a lightweight classifier module, for example:

- `butler/request_classifier.py`

Responsibilities:

- decide whether a request should run in foreground or background
- optionally classify job type

Initial implementation should be rule-based, not LLM-based.

Suggested initial background triggers:

- KiCad / PCB / schematic / CAD design requests
- anything explicitly asking Butler to "work on" something
- requests expected to need multi-step tool use
- requests that call out creation, generation, planning, or execution over time

Examples:

- "Design a basic RLC circuit" -> background
- "What time is it?" -> foreground
- "Turn on the TV" -> foreground
- "Research components for a buck converter and draft the schematic" -> background

### Session Notification Layer

Create a session-aware notification module, for example:

- `butler/session_events.py`

Responsibilities:

- track active listeners per session
- hold pending events for disconnected clients
- publish job events such as:
  - job accepted
  - progress update
  - waiting for user input
  - completed
  - failed

## Persistence Strategy

The architecture should assume jobs outlive individual client connections.

That means job data should not live only in memory.

Recommended progression:

### Phase 1

Store jobs in memory plus JSON snapshots on disk.

Possible location:

- `data/jobs/`

This is enough for a prototype and survives process restarts if reloaded at boot.

### Phase 2

Move jobs and session event records to SQLite.

Benefits:

- easier querying by session
- easier restart recovery
- easier worker coordination
- easier debugging and inspection

For this project, SQLite is likely the best default before introducing anything heavier.

## MCP Strategy

This architecture works best if background workers own the expensive MCP usage.

Recommended rule:

- the conversational lane should not need to eagerly initialize heavy MCPs
- the background lane should initialize MCP clients only when a job actually needs them

Longer term, support specialized worker pools:

- default worker pool for general MCP tasks
- KiCad worker pool for CAD jobs
- future robotics/home automation pool if those tools need isolation

This avoids forcing every user interaction to pay the startup cost of every MCP server.

## API And Message Flow Changes

### Current Text Flow

Current `/chat` behavior:

1. receive request
2. put a `text_cmd` message onto `central_queue`
3. block on `reply_queue`
4. return final response

### Proposed Text Flow

Extend `/chat` to support two response modes:

- synchronous foreground reply
- immediate background acknowledgment

Suggested response shape for background jobs:

```json
{
  "ok": true,
  "mode": "background",
  "session_id": "browser-session",
  "job_id": "job_123",
  "response": "I can do that. I'm working on it now.",
  "status": "queued"
}
```

Suggested response shape for foreground replies:

```json
{
  "ok": true,
  "mode": "foreground",
  "session_id": "browser-session",
  "response": "The current weather is 72F and sunny."
}
```

### New Suggested Endpoints

Add endpoints like:

- `POST /chat`
- `GET /jobs/{job_id}`
- `GET /sessions/{session_id}/jobs`
- `POST /jobs/{job_id}/cancel`
- `POST /jobs/{job_id}/input`
- `GET /sessions/{session_id}/events`

The first implementation can use polling.

Better follow-up options:

- Server-Sent Events
- WebSocket

For simplicity, SSE is probably the cleanest first push model for text clients.

## Voice Flow Changes

Voice should use the same job model as text.

Suggested behavior:

1. user speaks a request
2. Butler transcribes it
3. classifier marks it foreground or background
4. if background:
   - Butler immediately speaks a short acknowledgment
   - background worker starts the job
5. when the job completes:
   - if the voice session is active, Butler can speak the completion
   - otherwise store a pending session event and deliver it on next interaction

Important rule:

Do not require the original voice connection to remain open for the job to finish.

## Session Semantics

Session continuity matters more in the target architecture.

Recommended rule:

- each user-facing client interaction maps to a `session_id`
- background jobs are always attached to a `session_id`
- results are delivered to that session, not only to the original HTTP request

This supports:

- local voice on the server
- text from remote machines
- multiple remote clients sharing the same session intentionally

It also enables questions like:

- "What are you working on?"
- "Show me the result from earlier."
- "Cancel that KiCad job."

## Changes To Central Queue Messaging

Current message types are:

- `voice_cmd`
- `text_cmd`
- `status`
- `event`
- `ui`

Add explicit job-related message types, for example:

- `job_submit`
- `job_update`
- `job_complete`
- `job_failed`
- `job_cancel`
- `job_input`

Or keep the queue simpler and move most job coordination into `JobManager` directly.

Recommendation:

- keep client request ingestion in the central queue
- let `JobManager` own job lifecycle internally
- use explicit session event publishing for updates

This avoids overloading the router with too many responsibilities.

## Router Refactor Direction

The current router is a single blocking dispatcher. It should become a traffic coordinator instead.

Recommended responsibilities for the refactored router:

- receive incoming messages
- refresh status state
- send lightweight requests to the foreground path
- send long-running requests to `JobManager`
- publish immediate acknowledgments

Recommended responsibilities that should move out of the router:

- full long-running LLM/MCP execution
- retries for heavy jobs
- job persistence logic

## Foreground Path Design

Keep a synchronous path for lightweight commands.

Good foreground candidates:

- simple Q and A with no heavy MCP use
- status queries
- local house-control actions with fast tools
- short "do you want me to continue?" follow-ups

The foreground path can continue using `butler.agent.runtime.run_command(...)` at first.

Longer term, it may be worth adding:

- a lightweight runtime mode with a reduced tool surface
- optional no-MCP or low-MCP fast path

## Background Path Design

The background worker should run something close to a full agent cycle, but without blocking the request thread.

Suggested sequence:

1. load job record
2. mark `starting`
3. optionally initialize required MCPs
4. build an execution context from:
   - request text
   - session context
   - status snapshot
   - prior job artifacts if relevant
5. run the LLM and tool loop
6. persist progress updates during execution
7. persist final artifacts and summary
8. publish session completion event

## Progress Reporting

Background jobs should not be silent for long stretches.

Add periodic progress updates such as:

- "Starting KiCad tools"
- "Creating project"
- "Searching symbol libraries"
- "Drafting initial schematic"
- "Validating design"

The first version can emit coarse progress messages only.

Later versions can emit structured progress:

- percent complete
- current stage
- current tool
- elapsed time

## Handling User Follow-Ups During A Running Job

This is one of the most important interaction rules.

If the user says:

- "How is that going?"
- "Cancel that."
- "Use a 10uH inductor instead."

Butler should not treat that as unrelated free text.

Recommended behavior:

- session-aware queries should inspect active jobs first
- modification requests should either:
  - mutate a queued job
  - pause and request confirmation
  - spawn a follow-up revision job after completion

Do not let two jobs mutate the same project blindly in parallel.

## Resource Locking

Some jobs will target shared resources:

- a KiCad project directory
- a physical device
- a single MCP server instance

Introduce lightweight locks keyed by resource, for example:

- `kicad_project:/path/to/project`
- `device:living-room-tv`

Jobs that need a locked resource should either:

- wait in queue
- fail fast
- request user confirmation

This will matter more as more MCP tools are added.

## Failure Handling

Background jobs need first-class failure behavior.

Recommended rules:

- failures should be persisted
- failures should produce a user-readable summary
- retry policy should be explicit per job type
- jobs that need user input should enter `waiting_for_input`, not `failed`

Example failure summary:

"I couldn't finish the RLC circuit draft because the KiCad worker failed while loading symbol libraries. The job is saved and can be retried."

## Suggested Rollout Phases

### Phase 0: Architecture Prep

Goal:

- isolate the current synchronous path enough to introduce a background path cleanly

Tasks:

- create `docs/` documentation
- add module boundaries for job management and classification
- avoid embedding more logic inside `router.py`

### Phase 1: Job Model And In-Memory Background Worker

Goal:

- support immediate acknowledgment plus background execution inside one process

Tasks:

- add `JobManager`
- add background worker thread
- add request classifier
- add `mode: foreground|background` response format
- add `GET /jobs/{job_id}`

Outcome:

- text clients can receive immediate job acceptance
- background work runs without blocking the request thread

### Phase 2: Session Event Delivery

Goal:

- let clients receive completion and progress updates

Tasks:

- add session event store
- add polling endpoint or SSE endpoint
- update browser chat page to show job status updates
- add voice session completion announcements

Outcome:

- users do not have to manually retry or guess when work is done

### Phase 3: MCP Isolation And Lazy Startup

Goal:

- keep heavy MCP startup entirely off the conversational path

Tasks:

- ensure foreground path does not eagerly initialize heavy MCPs
- move KiCad MCP ownership to background workers
- optionally create worker pools by capability

Outcome:

- non-KiCad interactions stay fast
- KiCad startup no longer blocks simple chat

### Phase 4: Durable Persistence

Goal:

- survive process restarts reliably

Tasks:

- move job storage to SQLite
- reload queued/running jobs at startup
- define restart semantics for interrupted jobs

Outcome:

- Butler behaves more like a real long-lived service

### Phase 5: Advanced Scheduling

Goal:

- support concurrency, prioritization, and smarter dispatch

Tasks:

- priority queue
- per-resource locks
- per-worker capability routing
- retry/backoff policies by job type

## Suggested Initial File Additions

Possible new files:

- `butler/jobs.py`
- `butler/background_worker.py`
- `butler/request_classifier.py`
- `butler/session_events.py`
- `butler/job_store.py`

Possible files to modify:

- `butler/router.py`
- `butler/text/server.py`
- `butler/voice/agent.py`
- `butler/messages.py`
- `butler/agent/runtime.py`
- `butler/main.py`

## Suggested First Implementation Slice

If this work is being done incrementally, the best first slice is:

1. add a rule-based classifier
2. add `JobManager` with in-memory storage
3. add a single background worker thread
4. return immediate acknowledgments for background jobs
5. add `GET /jobs/{job_id}`

This is the smallest slice that proves the core interaction model.

Example result:

- user submits: "Design a basic RLC circuit"
- server responds immediately with `job_id`
- worker runs the heavy KiCad flow
- user polls job status and gets final result

After that works, add session event delivery and voice completion announcements.

## Recommended Non-Goals For The First Version

Avoid these in the first implementation:

- distributed workers
- external message brokers
- perfect multi-user conflict resolution
- generalized workflow DAGs
- automatic merging of concurrent job edits into the same project

The first version should optimize for:

- immediate acknowledgment
- non-blocking long-running jobs
- persistent job visibility
- clean session semantics

## Open Questions To Resolve Before Implementation

1. Should one Butler session allow multiple concurrent background jobs by default?
2. Should KiCad jobs share one persistent MCP instance or get isolated worker-specific instances?
3. What is the preferred completion delivery for remote text clients: polling, SSE, or WebSocket?
4. What should happen if the user continues chatting while a long-running job is modifying a project?
5. Should voice completion be spoken automatically or only when the user asks for status?

## Recommendation Summary

For Butler, the recommended direction is:

- keep a lightweight foreground path for quick interactions
- add a durable background job system for long-running MCP work
- attach jobs to sessions, not just open HTTP requests
- lazily initialize heavy MCPs in background workers
- add client-visible progress and completion events

This approach matches the target architecture much better than prewarming all MCPs and keeping the entire system synchronous.
