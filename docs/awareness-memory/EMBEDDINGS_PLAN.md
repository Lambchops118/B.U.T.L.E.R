# Embeddings / Semantic Memory Search — Implementation Plan

Status: **planned** (not yet implemented). Owner-authorized to defer, 2026-07-20.

## Summary

The awareness long-term memory embedding model (`nomic-embed-text`, the default
for `TALOS_AWARENESS_EMBEDDING_MODEL`) is **not installed in Ollama**, so every
`work_type=embedding` outbox job fails and dead-letters. Semantic (vector)
memory search silently degrades to keyword/full-text only. Reminders, voice
alerts, and device commands are unaffected — this is memory-recall quality only.

## Current state (observed 2026-07-20)

- `ollama list`: `hermes3:8b`, `qwen3:14b`, `mb-core-v1` — **no `nomic-embed-text`**.
- Awareness DB: 4 dead-lettered `embedding` outbox rows.
- `GET /memory/search` works via `tsvector`; `vector_used` is `false`; the
  `search_memory` MCP tool returns keyword matches only.
- GPUs available: RTX 5080 (16 GB, runs the chat model) and RTX 2060 (6 GB, idle).

## What to implement

1. **Placement.** Either:
   - `ollama pull nomic-embed-text` on the main instance (simplest; the model is
     ~300 MB so it co-exists with the chat model on the 5080), **or**
   - run a second Ollama instance pinned to the RTX 2060
     (`CUDA_VISIBLE_DEVICES=1`, its own port) and point
     `TALOS_AWARENESS_OLLAMA_HOST` at it, to keep embeddings off the chat GPU.
   The 2060 is not required for performance (the embed model is tiny and the chat
   GPU has headroom); it is only an isolation nicety.
2. **Dimension check.** `nomic-embed-text` = 768, matching
   `TALOS_AWARENESS_EMBEDDING_DIMENSION` (768) and the pgvector column. Changing
   the model family requires a migration + full re-embedding.
3. **Backfill.** Retry the dead-lettered embedding jobs
   (`POST /outbox/{id}/retry`) and confirm embeddings populate and searches
   report `vector_used=true`.
4. **Observability.** Surface embedding backlog / last-success in
   `/health/components` or `/metrics` so a missing model is obvious rather than
   only showing up as silent dead-letters.

## Acceptance

- `search_memory` returns semantically-ranked results with `vector_used=true`.
- No embedding dead-letters accumulate under normal operation.

## Interim workaround (until implemented)

Set `TALOS_AWARENESS_EMBEDDING_MODEL=` (empty) in `settings.env` so the backend
runs full-text-only cleanly and stops queuing/dead-lettering embedding work.

## References

- Phase 6 memory design: [`talos/awareness/README.md`](../../talos/awareness/README.md) (Long-term memory section)
- Proactive-presence work: [`SESSION_HANDOFF_2026-07-20_PROACTIVE_PRESENCE.md`](SESSION_HANDOFF_2026-07-20_PROACTIVE_PRESENCE.md)
