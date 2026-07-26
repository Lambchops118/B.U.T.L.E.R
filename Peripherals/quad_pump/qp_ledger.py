# Part of TALOS - Monkey Butler Device Operations System
#
# Quad pump firmware: bounded, persistent command ledger.
#
# Duplicate QoS 1 deliveries and awareness retries must never produce a second
# watering cycle. The ledger records the outcome of recent command IDs so a
# duplicate replays the recorded acknowledgement instead of moving a relay.
#
# Persistence exists so a reset *during* the awareness retry window cannot turn
# one watering request into two. Entries are stored as an ordered list rather
# than relying on dict ordering, which MicroPython does not guarantee.
#
# Pure logic apart from file I/O, which is injected for host tests.

import json

import qp_config as config

PHASE_ACCEPTED = "accepted"
PHASE_FINAL = "final"


class CommandLedger(object):
    def __init__(self, storage=None, capacity=None):
        self._storage = storage if storage is not None else FileStorage()
        self._capacity = capacity or config.LEDGER_CAPACITY
        self._entries = []  # list of dicts, oldest first
        self._writes = 0
        self._load()

    # --- reads ---------------------------------------------------------------

    def find(self, command_id):
        for entry in self._entries:
            if entry.get("command_id") == command_id:
                return entry
        return None

    def outcome(self, command_id):
        """Recorded final outcome for a completed command, or None."""
        entry = self.find(command_id)
        if entry is None or entry.get("phase") != PHASE_FINAL:
            return None
        return entry

    def entries(self):
        return list(self._entries)

    @property
    def write_count(self):
        return self._writes

    # --- writes --------------------------------------------------------------

    def accept(self, command_id, action, channel):
        """Record that a command was accepted, before the relay moves.

        Durable intent precedes the physical effect: if power is lost between
        this write and completion, boot recovery finalizes the entry as stopped
        rather than replaying the run.
        """
        entry = self.find(command_id)
        if entry is not None:
            return entry
        entry = {
            "command_id": command_id,
            "action": action,
            "channel": channel,
            "phase": PHASE_ACCEPTED,
        }
        self._entries.append(entry)
        self._trim()
        self._save()
        return entry

    def finalize(self, command_id, ok, result, channel=None, relay_state=None, fuse_state=None, error=None):
        entry = self.find(command_id)
        if entry is None:
            entry = {"command_id": command_id, "channel": channel}
            self._entries.append(entry)
        entry["phase"] = PHASE_FINAL
        entry["ok"] = bool(ok)
        entry["result"] = result
        if channel is not None:
            entry["channel"] = channel
        if relay_state is not None:
            entry["relay_state"] = relay_state
        if fuse_state is not None:
            entry["fuse_state"] = fuse_state
        if error is not None:
            entry["error"] = str(error)[:200]
        self._trim()
        self._save()
        return entry

    def recover_unfinished(self):
        """Finalize entries interrupted by a reset and return them.

        Anything still ``accepted`` at boot had its relay driven off by the
        reset itself. It is reported truthfully as a failure so the awareness
        request fails instead of silently timing out, and — critically — it is
        never resumed as a new run.
        """
        recovered = []
        changed = False
        for entry in self._entries:
            if entry.get("phase") == PHASE_ACCEPTED:
                entry["phase"] = PHASE_FINAL
                entry["ok"] = False
                entry["result"] = config.RESULT_STOPPED
                entry["relay_state"] = "off"
                entry["error"] = "device reset during run; relay driven off"
                recovered.append(entry)
                changed = True
        if changed:
            self._save()
        return recovered

    # --- internals -----------------------------------------------------------

    def _trim(self):
        while len(self._entries) > self._capacity:
            self._entries.pop(0)

    def _save(self):
        self._writes += 1
        try:
            self._storage.write(json.dumps({"version": 1, "entries": self._entries}))
        except Exception:
            # A full or failing filesystem must not stop the pump logic. The
            # in-memory ledger still deduplicates within this boot.
            pass

    def _load(self):
        try:
            raw = self._storage.read()
        except Exception:
            raw = None
        if not raw:
            return
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            # Corrupt ledger: start empty rather than trusting partial data.
            self._entries = []
            return
        if not isinstance(body, dict):
            return
        entries = body.get("entries")
        if not isinstance(entries, list):
            return
        clean = []
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("command_id"), str):
                clean.append(entry)
        self._entries = clean[-self._capacity:]


class FileStorage(object):
    """Atomic-ish flash persistence: write temp, replace, so a power loss
    mid-write leaves either the previous file or a recoverable temp file.

    Flash wear tradeoff: at most two writes per command (accept, finalize),
    bounded by the action cooldown. At one watering per minute that is well
    inside the endurance of the Pico's flash for the lifetime of the device.
    """

    def __init__(self, path=None, temp_path=None):
        self._path = path or config.LEDGER_PATH
        self._temp = temp_path or config.LEDGER_TEMP_PATH

    def read(self):
        for candidate in (self._path, self._temp):
            try:
                handle = open(candidate, "r")
            except OSError:
                continue
            try:
                return handle.read()
            finally:
                handle.close()
        return None

    def write(self, text):
        import os

        handle = open(self._temp, "w")
        try:
            handle.write(text)
        finally:
            handle.close()
        try:
            os.remove(self._path)
        except OSError:
            pass
        os.rename(self._temp, self._path)


class MemoryStorage(object):
    """Non-persistent storage for host tests and simulated restarts."""

    def __init__(self, initial=None):
        self.data = initial

    def read(self):
        return self.data

    def write(self, text):
        self.data = text
