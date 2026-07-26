# Part of TALOS - Monkey Butler Device Operations System
#
# Template for the device-local secrets file. Copy this to ``qp_secrets.py``
# on the Pico's filesystem and fill in the real values there.
#
# ``qp_secrets.py`` is git-ignored and must never be committed.
#
# IMPORTANT: the previous firmware had the live Wi-Fi SSID and password
# committed in ``main.py``. Deleting them from the current file does not remove
# them from Git history — rotate the Wi-Fi password (and any broker credential
# that was ever committed) as part of deploying this firmware.

WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-wifi-password"

MQTT_BROKER = "192.168.1.160"
MQTT_PORT = 0  # 0 selects 1883 (or 8883 with TLS) in umqtt.simple

# Leave as None while the broker still allows anonymous connections. Populate
# both once broker authentication is enabled (see
# docs/awareness-memory/BROKER_HARDENING_PLAN.md).
MQTT_USER = None
MQTT_PASSWORD = None
