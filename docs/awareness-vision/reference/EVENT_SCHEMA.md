# Vision Event Schema (draft, v1)

Vision events reuse the canonical `EventEnvelope`
([`talos/awareness/schemas/events.py`](../../../talos/awareness/schemas/events.py))
unchanged. This document defines the **`vision.*` event types and their
`payload` shapes**. Payloads are strictly versioned via `payload.payload_version`
so they can evolve without touching the envelope's `schema_version`.

Status: **DRAFT** — to be ratified at the end of Phase V0. Field names may be
adjusted to repository convention during V0; the semantic distinctions,
confidence handling, and negative-space rules are fixed by `VIN-02`/`VIN-06`.

## Envelope conventions for the vision source

| Field | Value |
|---|---|
| `source_id` | `vision_living_room` (one registered source per camera) |
| `source_type` | `vision` |
| `transport` | `mqtt` |
| `location_id` | the room, e.g. `living_room` |
| `provenance.topic_or_endpoint` | `vision/living_room/<event>` |
| `provenance.clock_quality` | `device_synced` **only if** the worker host is NTP-synced; otherwise `server_received` |
| `provenance.software_version` | worker version |
| `provenance.metadata` | `{detector, detector_weights, pose_weights, reid_pack, debounce_window_s}` — model + weights versions for auditability (`VIN-06`) |
| `observed_at` | frame capture time (trusted only under `device_synced`) |
| `sequence` / `source_boot_id` | monotonic per boot (`VIN-07`, dedup/ordering) |
| `confidence` | stage confidence (detection/track/match), `0.0`–`1.0` |

### Owned topics

```
vision/living_room/presence
vision/living_room/occupancy
vision/living_room/activity
vision/living_room/identity
```

The source's `allowed_topics` is `["vision/living_room/#"]`. Publishing on any
other topic is dead-lettered by the pipeline; a payload claiming a different
`source_id` is rejected as spoofing (existing `registry/sources.py` behavior).

## Negative space — never present in any vision payload (`VIN-02`)

- image bytes, base64 image/thumbnail data, or any pixel data
- filesystem paths to frames or crops
- raw biometric embedding vectors (face or body)
- bounding-box crops of people

Bounding boxes *may* appear only as normalized coordinates for debugging **if
explicitly enabled**, and are off by default. Track ids are ephemeral per-boot
integers, not stable identifiers of people.

---

## `vision.presence` — room occupied/vacant (STATE)

Classification: `UPDATE_CURRENT_STATE` + `STORE_HISTORY`. Emitted on transition
only (debounced), not per frame.

```jsonc
{
  "payload_version": 1,
  "state": "occupied",          // "occupied" | "vacant"
  "person_count": 2,             // integer >= 0
  "track_ids": [7, 9]            // ephemeral per-boot ids, non-persistent
}
```

State mapping: `entity_id = "living_room"`, `property_name = "occupancy"`,
`value = state`. (Open question `VOQ-3`: model the room as an entity vs. a
location-scoped state row.)

## `vision.occupancy` — headcount over time (TELEMETRY)

Classification: `STORE_TELEMETRY`. Numeric; lands in `sensor_measurements`
(hypertable + caggs). Emitted on change and at a low heartbeat interval.

```jsonc
{
  "payload_version": 1,
  "count": 2,                    // measurement value
  "window_seconds": 5.0          // aggregation/debounce window the count reflects
}
```

Maps to: `measurement_name = "occupancy_count"`, `value_double = count`,
`unit = "persons"`, `quality` from `confidence`.

## `vision.activity` — coarse activity (STATE + HISTORY)

Classification: `UPDATE_CURRENT_STATE` + `STORE_HISTORY`. Pose-derived, coarse,
debounced.

```jsonc
{
  "payload_version": 1,
  "track_id": 7,
  "activity": "sitting",         // sitting | standing | walking | lying_down | absent | unknown
  "activity_confidence": 0.82    // redundant with envelope confidence; kept for clarity
}
```

`lying_down` is a candidate signal for a future safety phase (fall / motionless)
but V2 only reports the coarse label; alert semantics are deferred.

## `vision.identity` — enrolled household member (STATE + HISTORY, SENSITIVE)

Classification: `UPDATE_CURRENT_STATE` + `STORE_HISTORY`. **Restricted
sensitivity, evidence-class, shorter retention, kill-switchable** (`VIN-05`).

```jsonc
{
  "payload_version": 1,
  "track_id": 7,
  "person_id": "resident_a",     // slug referencing the enrolled gallery, NOT biometric data
  "match_score": 0.71,           // cosine similarity to gallery template
  "modality": "face"             // "face" | "body"
}
```

- `severity`: `info`; `confidence`: the match score; `retention_class`:
  `identity_short` (proposed).
- Below the configured match threshold, **no `vision.identity` event is
  emitted** — the track remains anonymous presence (`VIN-06`).
- With the identity kill switch off, this event type is never produced and the
  re-ID stage does not run (`VIN-05`/`VIN-08`).
- `person_id` references a row in the enrolled gallery; the biometric template
  itself lives encrypted outside the event stream and is never transmitted.

## Versioning rules

- Additive optional fields: allowed within the same `payload_version`.
- Removed/renamed/retyped fields or changed semantics: bump `payload_version`
  and update the ingestion normalizer + this document together.
- Envelope `schema_version` changes only if the envelope itself changes (out of
  scope here).
