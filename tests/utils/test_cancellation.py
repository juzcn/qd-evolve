"""Tests for qd_evolve.utils.cancellation — CancellationToken, CancelledError."""

import pytest

from qd_evolve.utils.cancellation import CancellationToken, CancelledError


class TestCancelledError:
    def test_is_exception(self):
        err = CancelledError()
        assert isinstance(err, Exception)

    def test_can_be_raised(self):
        with pytest.raises(CancelledError):
            raise CancelledError()

    def test_can_be_caught(self):
        try:
            raise CancelledError()
        except CancelledError:
            pass  # expected


class TestCancellationToken:
    def test_initial_not_cancelled(self):
        token = CancellationToken()
        assert token.is_cancelled is False

    def test_check_does_not_raise_when_not_cancelled(self):
        token = CancellationToken()
        token.check()  # should not raise

    def test_cancel_sets_is_cancelled(self):
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled is True

    def test_cancel_is_idempotent(self):
        """cancel() can be called multiple times without error."""
        token = CancellationToken()
        token.cancel()
        token.cancel()
        token.cancel()
        assert token.is_cancelled is True

    def test_check_raises_after_cancel(self):
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            token.check()

    def test_cancel_then_check_raises_multiple_times(self):
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            token.check()
        with pytest.raises(CancelledError):
            token.check()

    def test_multiple_tokens_are_independent(self):
        t1 = CancellationToken()
        t2 = CancellationToken()
        t1.cancel()
        assert t1.is_cancelled is True
        assert t2.is_cancelled is False
        t2.check()  # should not raise

    def test_cancel_before_check(self):
        """cancel → check raises immediately (doesn't need a running thread)."""
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            token.check()
