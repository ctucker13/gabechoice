import asyncio
import time


class TokenBucket:
    """Async token bucket rate limiter. acquire() blocks until a token is available."""

    def __init__(self, rate: float):
        self.rate = rate          # tokens per second
        self._tokens = rate       # start with one full burst slot
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        wait = 0.0
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.rate, self._tokens + elapsed * self.rate)
            self._last = now
            if self._tokens < 1:
                # Advance virtual clock so the next caller queues correctly.
                deficit = 1.0 - self._tokens
                wait = deficit / self.rate
                self._last = now + wait
                self._tokens = 0.0
            else:
                self._tokens -= 1.0
        if wait > 0:
            await asyncio.sleep(wait)
