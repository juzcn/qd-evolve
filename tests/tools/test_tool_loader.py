"""Tests for qd_evolve.tools.tool_loader — activate_func handler."""

from unittest.mock import patch



class TestActivateFunc:
    def test_load_existing_tool(self, registry_with_echo):
        registry_with_echo.register("fetch", "Fetch URL", lambda u: u,
                                    {"type": "object", "properties": {"url": {"type": "string"}}})
        with patch("qd_evolve.tools.tool_loader.get_registry", return_value=registry_with_echo):
            from qd_evolve.tools.tool_loader import _load_tool_detail
            result = _load_tool_detail("fetch")
            assert "activated" in result
            assert "fetch" in result

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
            assert "already active" in result

    def test_not_found_lists_available_tools(self, registry_with_echo):
        with patch("qd_evolve.tools.tool_loader.get_registry", return_value=registry_with_echo):
            from qd_evolve.tools.tool_loader import _load_tool_detail
            result = _load_tool_detail("nonexistent")
            assert "not found" in result
            assert "echo" in result  # available tools listed

    def test_set_preload_tools_accumulates(self):
        from qd_evolve.tools.tool_loader import set_preload_tools
        from qd_evolve.tools import tool_loader as mod
        old = mod._preload_tools
        mod._preload_tools = set()
        try:
            set_preload_tools({"a"})
            set_preload_tools({"b", "c"})
            assert "a" in mod._preload_tools
            assert "b" in mod._preload_tools
            assert "c" in mod._preload_tools
        finally:
            mod._preload_tools = old

    def test_load_tool_returns_confirmation_message(self, registry_with_echo):
        registry_with_echo.register("fetch", "Fetch URL", lambda u: u,
                                    {"type": "object", "properties": {"url": {"type": "string"}}})
        with patch("qd_evolve.tools.tool_loader.get_registry", return_value=registry_with_echo):
            from qd_evolve.tools.tool_loader import _load_tool_detail
            result = _load_tool_detail("fetch")
            assert "Tool 'fetch' activated" in result
            assert "Fetch URL" in result
            assert "schema available" in result

    def test_load_tool_with_nested_schema(self, registry_with_echo):
        complex_schema = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "properties": {
                        "nested": {"type": "string"},
                    },
                },
            },
            "required": ["options"],
        }
        registry_with_echo.register("complex", "Complex tool", lambda o: o, complex_schema)
        with patch("qd_evolve.tools.tool_loader.get_registry", return_value=registry_with_echo):
            from qd_evolve.tools.tool_loader import _load_tool_detail
            result = _load_tool_detail("complex")
            assert "Tool 'complex' activated" in result
            assert "Complex tool" in result