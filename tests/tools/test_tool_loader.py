"""Tests for qd_evolve.tools.tool_loader — load_func handler."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestLoadFunc:
    def test_load_existing_tool(self, registry_with_echo):
        registry_with_echo.register("fetch", "Fetch URL", lambda u: u,
                                    {"type": "object", "properties": {"url": {"type": "string"}}})
        with patch("qd_evolve.tools.tool_loader.get_registry", return_value=registry_with_echo):
            from qd_evolve.tools.tool_loader import _load_tool_detail
            result = _load_tool_detail("fetch")
            data = json.loads(result)
            assert data["name"] == "fetch"

    def test_load_nonexistent_tool(self, registry_with_echo):
        with patch("qd_evolve.tools.tool_loader.get_registry", return_value=registry_with_echo):
            from qd_evolve.tools.tool_loader import _load_tool_detail
            result = _load_tool_detail("nonexistent")
            assert "not found" in result

    def test_load_preloaded_tool(self, registry_with_echo):
        with patch("qd_evolve.tools.tool_loader.get_registry", return_value=registry_with_echo):
            from qd_evolve.tools.tool_loader import _load_tool_detail, set_preload_tools
            set_preload_tools({"echo"})
            result = _load_tool_detail("echo")
            assert "already preloaded" in result