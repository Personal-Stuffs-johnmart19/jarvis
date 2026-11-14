"""
loop_guard.py — Anti-loop protection for Jarvis's tool calls.
--------------------------------------------------------------
Guards against two failure modes while the model drives tools in a live
session:

  1. Identical tool + arguments called repeatedly (e.g. stuck on retrying the
     same failing action) — blocked after `max_identical` consecutive calls.
  2. Ping-pong loops between exactly two tools (A, B, A, B, ...) — blocked when
     the alternation repeats `ping_pong` times.

When a loop is detected, `check()` returns a short instruction string for the
model ("stop and report the last result"); the caller passes it back as the
tool result so the model can break the cycle instead of apologizing blindly.
"""

import json
import hashlib


class LoopGuard:

    def __init__(self, max_identical: int = 3, ping_pong: int = 3):
        self.max_identical = max_identical
        self.ping_pong     = ping_pong
        self._last_sig     = None
        self._same_count   = 0
        self._recent       = []

    @staticmethod
    def _signature(name: str, args: dict) -> tuple:
        try:
            compact = json.dumps(dict(args or {}), sort_keys=True, default=str)
        except Exception:
            compact = str(args)
        return (name, hashlib.sha256(compact.encode("utf-8")).hexdigest())

    def check(self, name: str, args: dict) -> str | None:
        """Record a tool call; return a block instruction string if a loop is
        detected, otherwise None."""
        sig = self._signature(name, args)

        if sig == self._last_sig:
            self._same_count += 1
        else:
            self._last_sig   = sig
            self._same_count = 1

        if self._same_count >= self.max_identical:
            return (
                f"Loop guard: tool '{name}' was called {self._same_count} times "
                f"in a row with identical arguments. This is a loop — do NOT call "
                f"'{name}' again. Stop and report the current state to the user."
            )

        self._recent.append(sig)
        if len(self._recent) > max(8, 2 * self.ping_pong):
            self._recent.pop(0)

        rounds = max(2, self.ping_pong)
        if len(self._recent) >= rounds * 2:
            recent = self._recent[-rounds * 2:]
            a, b = recent[:2]
            if a != b and recent == [a, b] * rounds:
                return (
                    f"Loop guard: detected a ping-pong loop between '{a[0]}' and "
                    f"'{b[0]}'. Stop alternating these calls and report the last result."
                )
        return None

    def reset(self) -> None:
        self._last_sig   = None
        self._same_count = 0
        self._recent     = []
