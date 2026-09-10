import time

class StateStore:
    """In-memory store for status/event data with simple freshness decay."""

    def __init__(self):
        self._data = {}  # key -> (value, expires_at)

    def update_status(self, key, value, freshness: float):
        expires_at = time.time() + max(freshness, 0)
        self._data[key] = (value, expires_at)

    def snapshot(self) -> str:
        """Return a short summary string of non-stale status entries."""
        now = time.time()
        parts = []
        for k, (v, exp) in list(self._data.items()):
            if exp < now:
                del self._data[k]
                continue
            ttl = int(exp - now)
            parts.append(f"{k}:{v}({ttl}s)")
        return "; ".join(parts) if parts else "no recent status"
