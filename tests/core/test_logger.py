"""Tests for qd_evolve.core.logger — SharedFileHandler, setup_logging."""

import logging
from pathlib import Path

import pytest

from qd_evolve.core.logger import SharedFileHandler, setup_logging


class TestSharedFileHandler:
    def test_creates_log_file(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        log_file = tmp_path / "test.log"
        handler = SharedFileHandler(str(log_file))
        record = logging.LogRecord("test", logging.INFO, "", 0, "hello", (), None)
        handler.emit(record)
        handler.close()
        content = log_file.read_text(encoding="utf-8")
        assert "hello" in content

    def test_multiple_emits(self, tmp_path):
        log_file = tmp_path / "test.log"
        handler = SharedFileHandler(str(log_file))
        for i in range(5):
            record = logging.LogRecord("test", logging.INFO, "", 0, f"line {i}", (), None)
            handler.emit(record)
        handler.close()
        content = log_file.read_text(encoding="utf-8")
        for i in range(5):
            assert f"line {i}" in content

    def test_flush_after_emit(self, tmp_path):
        log_file = tmp_path / "test.log"
        handler = SharedFileHandler(str(log_file))
        record = logging.LogRecord("test", logging.INFO, "", 0, "flushed", (), None)
        handler.emit(record)
        content = log_file.read_text(encoding="utf-8")
        assert "flushed" in content
        handler.close()


class TestSetupLogging:
    def test_configures_logger(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir), level="DEBUG")
        logger = logging.getLogger("qd_evolve")
        assert logger.level == logging.DEBUG

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "new_logs"
        setup_logging(log_dir=str(log_dir), level="INFO")
        assert log_dir.exists()

    def test_writes_to_file(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir), level="INFO")
        logger = logging.getLogger("qd_evolve")
        logger.info("test message")
        log_files = list(log_dir.glob("*.log"))
        assert len(log_files) >= 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "test message" in content


class TestSharedFileHandlerError:
    def test_emit_handles_write_error(self, tmp_path):
        log_file = tmp_path / "test.log"
        handler = SharedFileHandler(str(log_file))
        # Break the baseFilename to trigger IOError
        handler.baseFilename = str(tmp_path / "nonexistent_dir" / "test.log")
        record = logging.LogRecord("test", logging.INFO, "", 0, "should fail", (), None)
        # Should not raise — error is handled via handleError
        handler.emit(record)
        handler.close()