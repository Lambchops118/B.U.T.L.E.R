# Vision Open Questions

Unresolved questions (`VOQ-*`) blocking or shaping later phases. Resolve at the
V0 review gate or as noted.

| ID | Question | Blocks | Owner input needed | Current lean |
|---|---|---|---|---|
| VOQ-1 | Weights licensing: is Ultralytics YOLO **AGPL-3.0** and the InsightFace `buffalo_l` **non-commercial** license acceptable for this deployment, or do we need permissively-licensed alternatives? | V1 (pin weights) | Yes | Acceptable for a private home deployment; confirm. |
| VOQ-2 | Deploy compute: **dedicated edge device (Jetson-class)** vs. **share the RTX 2060**? | V5 (may pre-order device) | Yes | Dedicated edge device for privacy isolation; decide after V1. |
| VOQ-3 | State modeling: is the room an **`entity`** (so `current_state` rows key on it) or is presence/activity state **location-scoped**? | V1 (migration shape) | Design | Register each room as an entity of type `room`; simplest fit for `current_state`. |
| VOQ-4 | Clock: will the deploy device run **NTP** (→ `device_synced`, trusted `observed_at`) or not (→ `server_received`)? | V1 (provenance) | Confirm | Dev Mac is synced; edge device should run NTP. |
| VOQ-5 | Identity retention: concrete duration for `identity_short` (e.g. 7/30/90 days) and whether presence/occupancy retain longer. | V3/V5 | Yes | Short identity retention, longer anonymous occupancy; propose 30 days identity. |
| VOQ-6 | Enrollment UX: CLI-only, or a small local GUI/tool? How many household members, and consent capture format? | V3 | Yes | CLI in V3; N members enrolled from short local captures. |
| VOQ-7 | Camera field of view / mounting in the living room — affects whether one camera covers the room and how presence debouncing is tuned. | V1 tuning | Info | Assess against the real camera in V1. |
| VOQ-8 | Should `foyer`/`kitchen` get cameras later? Affects whether topic/room scheme should be generalized now. | Future | Info | Design topics/rooms generically now (`vision/<room>/#`) so adding cameras is additive. |
| VOQ-9 | Body re-ID fallback (when face not visible): in scope for V3 or a later refinement? | V3 scope | Design | Start face-only in V3; add body re-ID as a follow-up. |
