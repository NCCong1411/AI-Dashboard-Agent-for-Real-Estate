"""Small, process-local login throttle for the single-worker public demo."""

from __future__ import annotations

from collections import deque
from threading import Lock
from time import monotonic


class LoginLimiter:
    def __init__(
        self, *, max_failures: int = 8, global_failures: int = 60,
        window_seconds: int = 300,
    ) -> None:
        self.max_failures = max_failures
        self.global_failures = global_failures
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._failures: dict[str, deque[float]] = {}
        self._all_failures: deque[float] = deque()
        self._pending: dict[str, int] = {}

    def begin(self, username: str) -> bool:
        key = username.casefold()
        now = monotonic()
        with self._lock:
            failures = self._failures.setdefault(key, deque())
            while failures and failures[0] <= now - self.window_seconds:
                failures.popleft()
            while self._all_failures and self._all_failures[0] <= now - self.window_seconds:
                self._all_failures.popleft()
            if (len(failures) >= self.max_failures
                    or len(self._all_failures) >= self.global_failures
                    or self._pending.get(key, 0) >= 2
                    or sum(self._pending.values()) >= 8):
                return False
            self._pending[key] = self._pending.get(key, 0) + 1
            return True

    def end(self, username: str, *, successful: bool) -> None:
        key = username.casefold()
        with self._lock:
            pending = self._pending.get(key, 0) - 1
            if pending > 0:
                self._pending[key] = pending
            else:
                self._pending.pop(key, None)
            if successful:
                self._failures.pop(key, None)
            else:
                failed_at = monotonic()
                self._failures.setdefault(key, deque()).append(failed_at)
                self._all_failures.append(failed_at)
            # Bound memory when bots send many distinct usernames.
            if len(self._failures) > 1024:
                oldest = next(iter(self._failures))
                self._failures.pop(oldest, None)


login_limiter = LoginLimiter()
