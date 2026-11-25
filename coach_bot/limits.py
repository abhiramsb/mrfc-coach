"""Cooldown + cached report text so repeat commands cost no tokens.

Backed by Store (Discord data channel + local file) so restarts keep the limits.
"""
import time


class Limiter:
    def __init__(self, store):
        self.store = store

    def get(self, key, window):
        """Return (cached_text, seconds_left) if still inside the window."""
        e = self.store.data["cache"].get(key)
        if e and time.time() - e["ts"] < window:
            return e["text"], int(window - (time.time() - e["ts"]))
        return None, 0

    async def put(self, key, text):
        if "cut off by the model" in text:  # don't lock in a truncated reply
            return
        self.store.data["cache"][key] = {"ts": time.time(), "text": text}
        await self.store.save()


def human(seconds):
    h, m = divmod(seconds // 60, 60)
    d, h = divmod(h, 24)
    return (f"{d}d " if d else "") + (f"{h}h " if h or d else "") + f"{m}m"
