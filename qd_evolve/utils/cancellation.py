"""Cooperative cancellation token for interruptible agent tasks.

Used by sub_agent_manager (cancel_sub_task) and A2A inproc transport
(cancel_task) to signal a running agent to stop at the next safe
checkpoint.
"""

from __future__ import annotations

import threading


class CancelledError(Exception):
    """Raised at checkpoints when a task has been cancelled."""


class CancellationToken:
    """Cooperative cancellation signal.

    Two-phase feedback:

    1. **Request** — ``cancel()`` sets the event.  The caller
       immediately knows whether cancellation was requested vs the
       task was already finished.
    2. **Acknowledge** — the running agent calls ``check()`` at safe
       boundaries, raises :exc:`CancelledError`, and the runner
       pushes a "cancelled" result back to the parent.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal cancellation.  Idempotent — safe to call multiple times."""
        self._event.set()

    def check(self) -> None:
        """Raise :exc:`CancelledError` if cancelled.

        Call at safe boundaries — before each LLM request, after each
        tool execution — anywhere it is safe to unwind the call stack.
        """
        if self._event.is_set():
            raise CancelledError()

    @property
    def is_cancelled(self) -> bool:
        """True once ``cancel()`` has been called (does not raise)."""
        return self._event.is_set()
