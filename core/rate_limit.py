"""Login rate limiting / lockout.

In-memory only. An edge box is a single-process deploy, so a process-local
dict is correct — we don't need Redis. State is lost on restart, which is
fine: a restart is itself a "release" event (the legitimate operator may have
just rebooted the box).

Tracking is keyed by (username, ip) so an attacker can't lock out a real user
just by hammering their username from elsewhere — the legitimate user from a
different IP keeps a clean tally.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class _Bucket:
    failures: list = field(default_factory=list)  # list of unix timestamps
    locked_until: float = 0.0  # 0 means not locked


class LoginGuard:
    """Sliding-window failure counter with lockout.

    A failure is recorded for (username, ip). If `max_attempts` failures
    occur within `window_seconds`, the (username, ip) pair is locked for
    `lockout_seconds`. Successful login clears the bucket.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 300,        # 5-min window for counting
        lockout_seconds: int = 300,       # 5-min lockout
    ):
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(1, int(window_seconds))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, ip: str) -> tuple[str, str]:
        return ((username or "").strip().lower(), (ip or "").strip())

    def _prune(self, b: _Bucket, now: float) -> None:
        cutoff = now - self.window_seconds
        b.failures = [t for t in b.failures if t >= cutoff]

    def is_locked(self, username: str, ip: str) -> tuple[bool, int]:
        """Return (locked, seconds_remaining). 0 if not locked."""
        now = time.time()
        with self._lock:
            b = self._buckets.get(self._key(username, ip))
            if not b:
                return False, 0
            if b.locked_until > now:
                return True, int(b.locked_until - now) + 1
            return False, 0

    def record_failure(self, username: str, ip: str) -> tuple[bool, int]:
        """Record a failed attempt. Returns (now_locked, seconds_remaining)."""
        now = time.time()
        with self._lock:
            key = self._key(username, ip)
            b = self._buckets.setdefault(key, _Bucket())
            self._prune(b, now)
            b.failures.append(now)
            if len(b.failures) >= self.max_attempts:
                b.locked_until = now + self.lockout_seconds
                return True, self.lockout_seconds
            return False, 0

    def record_success(self, username: str, ip: str) -> None:
        """Reset the bucket on a successful login."""
        with self._lock:
            self._buckets.pop(self._key(username, ip), None)

    def remaining_attempts(self, username: str, ip: str) -> int:
        """How many failures the user can still rack up before lockout."""
        now = time.time()
        with self._lock:
            b = self._buckets.get(self._key(username, ip))
            if not b:
                return self.max_attempts
            self._prune(b, now)
            return max(0, self.max_attempts - len(b.failures))

    def reset_all(self) -> None:
        """Test helper — wipe state."""
        with self._lock:
            self._buckets.clear()
