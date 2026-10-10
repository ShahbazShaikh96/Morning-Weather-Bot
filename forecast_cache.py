"""Small process-local read cache. Durable update tracking stays in Postgres."""
from threading import RLock
from time import monotonic


class ReadCache:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.entries = {}
        self.lock = RLock()

    def get(self, key, ttl, loader):
        # Coalesce simultaneous misses so several commands fetch only once.
        with self.lock:
            cached = self.entries.get(key)
            if cached is not None and self.clock() < cached[0]:
                return cached[1]
            value = loader()  # Failure propagates; expired data is never served.
            self.entries[key] = (self.clock() + ttl, value)
            return value

    def clear(self):
        with self.lock:
            self.entries.clear()
