# Inbound Phone Calls: ElevenLabs Agent ↔ Talos

Call the Twilio number, talk to the ElevenLabs voice agent, and have it know what
Talos knows and act through Talos. ElevenLabs runs the conversation (its own
LLM, STT, TTS); Talos decides who gets through and supplies knowledge before,
during, and after the call. Code:
[`butler/phone/inbound_server.py`](../../butler/phone/inbound_server.py).

```
Caller ─ Twilio ──voice webhook──▶ Funnel /phone ──▶ 127.0.0.1:8431
                                     │
  1. POST /phone/twilio/voice   gate on allowlist + StirVerstat; reject, or
                                register-call with ElevenLabs → TwiML connects
                                the caller to the agent (briefing + call token)
  2. POST /phone/tools/<tool>   recall / house_status / ask_talos, mid-call
  3. POST /phone/postcall       transcript + summary into memory, announced
```

## Security model

- **Gate:** a call reaches the agent only if the caller is in
  `BUTLER_PHONE_INBOUND_ALLOWED_CALLERS` **and** Twilio reports
  `StirVerstat=TN-Validation-Passed-A`, meaning the originating US carrier
  vouches that the caller is entitled to that number. Spoofed caller ID,
  B/C-attested, unverified, and international calls are rejected with
  `<Reject/>` before ElevenLabs is involved. No PIN or password.
- Every call's `StirVerstat` is logged (`[phone-inbound] incoming call from
  ...1234 StirVerstat=...`). If your own calls arrive with a variant such as
  `TN-Validation-Passed-A-Diverted`, add it to
  `BUTLER_PHONE_ACCEPTED_ATTESTATIONS`.
- The voice webhook requires a valid `X-Twilio-Signature`. Tools and the
  post-call webhook require `Authorization: Bearer <BUTLER_PHONE_INBOUND_TOKEN>`
  (or `?token=`, or a valid `ElevenLabs-Signature` for the post-call webhook).
- Admitted calls get a random `talos_call_token` dynamic variable. Tools send it
  back (filled by ElevenLabs, not its LLM) and refuse anything without a live
  token, so the tools only serve calls that passed the gate.
- `ask_talos` runs with **full** tool access, like typed chat: an admitted caller
  can turn lights off. Anyone you allowlist gets that power.
- Mid-call turns run in session `phone:<caller number>`, so later calls keep
  context and the post-call transcript lands in the same session.

## 1. Talos settings

`.env`: `TWILIO_AUTH_TOKEN` (already there for SMS), `ELEVENLABS_API_KEY`, and

```env
BUTLER_PHONE_INBOUND_TOKEN=<long random string>
BUTLER_PHONE_WEBHOOK_SECRET=<ElevenLabs post-call webhook secret>   # optional
```

`settings.env` (`BUTLER_PHONE_AGENT_ID` is already set):

```env
BUTLER_PHONE_INBOUND_ENABLED=1
BUTLER_PHONE_VOICE_PUBLIC_URL=https://<machine>.<tailnet>.ts.net/phone/twilio/voice
BUTLER_PHONE_INBOUND_ALLOWED_CALLERS=["+1<your cell>"]
```

Restart the main agent; it logs `Phone inbound listener on http://127.0.0.1:8431/phone`.

## 2. Public ingress

SMS already uses Funnel on `/` → 8430. Add a path for the phone listener:

```powershell
tailscale funnel --bg --set-path /phone http://127.0.0.1:8431/phone
```

Check: `https://<machine>.<tailnet>.ts.net/phone/health` returns `{"ok": true}`.

## 3. Twilio number

In the Twilio console, under the number's **Voice configuration**, set "A call
comes in" to Webhook, `HTTP POST`, with exactly `BUTLER_PHONE_VOICE_PUBLIC_URL`.
Optionally set the fallback URL to a TwiML Bin that says Talos is offline.

This replaces the voice URL that ElevenLabs' native number import configured.
Calls now reach ElevenLabs only through Talos's register-call request. If
ElevenLabs later overwrites the URL (for example, after re-importing the
number), set it back.

## 4. ElevenLabs agent

`BASE = https://<machine>.<tailnet>.ts.net/phone`. Dashboard label names may
differ slightly from these.

Talos passes these dynamic variables on every admitted call:

| Variable | Meaning |
|---|---|
| `talos_briefing` | Memory summaries, relevant facts, current house situation |
| `talos_call_token` | Per-call secret the tools send back |
| `talos_session_id` | `phone:<number>`; echoed back on the post-call webhook |

**System prompt** (starting point):

```text
You are Talos, the owner's home assistant, on a phone call with a verified caller.

What Talos knows right now:
{{talos_briefing}}

Rules:
- For anything about the owner, the house, or past conversations, use the
  briefing first, then `recall`. Never guess or invent.
- For live house state use `house_status`.
- For anything else, and for every action (lights, pumps, reminders...), use
  `ask_talos` with the caller's full request in their words. Never claim an
  action happened unless ask_talos says so.
- If a tool says Talos is still working, tell the caller and offer to check back.
- Keep replies short and conversational.
```

**Server tools.** For each one: method `POST`, URL `BASE/tools/<name>`, header
`Authorization: Bearer <TOKEN>` (store it as a secret), and set "wait for
response". In every tool, add a body parameter `call_token` filled from the
**dynamic variable** `talos_call_token` (not the LLM).

| Tool | LLM-filled body params | Description for the agent | Timeout |
|---|---|---|---|
| `recall` | `query` (string): what to look up | Search Talos's long-term memory about the owner and past conversations. | 10 s |
| `house_status` | none | Current house situation: who is home, devices, recent events. | 10 s |
| `ask_talos` | `request` (string): the caller's full question or instruction | Ask the main Talos agent a question or have it do something in the house. Slow. | 25 s |

Keep `BUTLER_PHONE_ASK_TIMEOUT` (default 15 s) below the `ask_talos` timeout so
Talos answers "still working" before ElevenLabs gives up.

**Post-call webhook** (workspace → Webhooks, post-call transcription):
`BASE/postcall?token=<TOKEN>`, or set its HMAC secret as
`BUTLER_PHONE_WEBHOOK_SECRET`.

## Verify

1. Call from your cell. The log shows your `StirVerstat`. If it isn't admitted,
   check that value against `BUTLER_PHONE_ACCEPTED_ATTESTATIONS`.
2. Ask "what's going on at home?" (house_status), "what did I tell you about
   X?" (recall), and "turn off the kitchen lights" (ask_talos).
3. Call from a number not on the allowlist: the call is rejected immediately.
4. After hanging up, the main agent announces the call, and
   `recent_phone_calls` shows it as inbound in session `phone:<cell>`.

## Known limits

- SHAKEN/STIR is deployed only in the US and France. Calls that are forwarded or
  routed through some networks can lose their A attestation.
- The briefing is a snapshot at call start; mid-call changes come only via tools.
- `ask_talos` queues behind any in-progress router turn and waits on the home
  GPU; expect several seconds.
- Call tokens are kept in memory: restarting Talos mid-call disables the tools
  for that call.
- Everything in the briefing and in tool results is sent to ElevenLabs.
