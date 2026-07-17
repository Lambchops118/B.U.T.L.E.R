# Broker Hardening Plan (OQ-B — owner-executed LAN work)

The Pi Mosquitto broker (`192.168.1.160:1883`) is assumed anonymous with no
ACLs or TLS (client-side evidence only; the broker config was never
verifiable from the off-LAN dev machine). Phase 8 delivers this plan instead
of live changes: every step below touches the Pi or devices on the LAN and
therefore requires owner execution. The awareness backend is already
prepared — username/password and TLS (CA + mutual) are configuration-only
(`TALOS_AWARENESS_MQTT_USERNAME/_PASSWORD/_TLS/_CA_PATH/...`).

## Current risk

- Anyone on the LAN can publish device commands (`quad_pump/*`, `fan/16`)
  and spoof `status/*` topics (ingestion's topic-ownership check limits the
  blast radius to registered topics, but cannot authenticate the sender).
- Wi-Fi credentials and the broker IP are committed in the Pico firmware
  files (`Peripherals/*/main.py`) — rotating credentials requires reflashing.

## Steps (in order; each is independently reversible)

1. **Verify current broker config** on the Pi:
   `cat /etc/mosquitto/mosquitto.conf /etc/mosquitto/conf.d/*` — confirm
   `allow_anonymous` state and listeners. Record findings in OPEN_QUESTIONS.
2. **Create credentials** (per client identity, not shared):
   `mosquitto_passwd -c /etc/mosquitto/passwd talos-awareness` then `-b` for
   `talos-scheduler`, `fan-pico`, `pump-pico`, `pi-display`.
3. **Add an ACL file** (`/etc/mosquitto/acl`):
   - `talos-awareness`: read `status/#`, `home/#`; write `quad_pump/#`,
     `fan/#`, `home/#` (action dispatch).
   - `fan-pico`: write `status/16`; read `fan/16`.
   - `pump-pico`: write `status/17`, `status/18`, `status/19`; read `quad_pump/#`.
   - `pi-display`: read `tv_display/#`.
4. **Enable auth in stages**: set `password_file`/`acl_file` with
   `allow_anonymous true` first; update every client with credentials
   (backend + scheduler via `.env`; firmware requires editing
   `Peripherals/{fan,quad_pump}/main.py` `umqtt` connect calls and
   reflashing — see the firmware constraint below); then flip
   `allow_anonymous false` and restart Mosquitto.
5. **TLS (optional, after auth works)**: self-signed CA on the Pi
   (`listener 8883` with `cafile/certfile/keyfile`), distribute the CA to
   clients. Note: the Pico `umqtt.simple` TLS support is limited; TLS may
   reasonably cover only the backend/scheduler/Pi clients, with the Picos
   remaining on the LAN listener — document whichever split is chosen.
6. **Rotate the committed Wi-Fi credentials** after reflashing (they are in
   git history; rotation, not deletion, is the fix).

## Constraints and cautions

- Firmware changes are currently out of scope by owner decision (ADR-014);
  steps 4's firmware part and 6 reopen that decision (OQ-C). Until then the
  broker cannot require auth without disconnecting both Picos.
- Both Picos share the client id `pico-w-client` (mutual-kick bug); assign
  unique ids in the same reflash.
- Test each step against the dockerized test broker first
  (`docker/mosquitto-test.conf` can mirror the passwd/ACL config).
- After enabling auth, confirm `/health/components` shows the backend
  reconnected and `ingestion.metrics` still advancing.
