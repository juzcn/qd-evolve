"""Tests for qd_evolve.core.registry — ToolRegistry, ToolDef, definitions, call."""

import pytest

from qd_evolve.core.registry import ToolRegistry, ToolDef, decode_output


class TestToolDef:
    def test_basic_creation(self):
        td = ToolDef(name="echo", description="Echo tool", handler=lambda s: s)
        assert td.name == "echo"
        assert td.enabled is True
        assert td.input_schema == {}

    def test_custom_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        td = ToolDef(name="add", description="Add", handler=lambda x: str(x), input_schema=schema)
        assert td.input_schema == schema


class TestToolRegistry:
    def test_register_and_get(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        td = registry.get("echo")
        assert td is not None
        assert td.name == "echo"

    def test_register_collision_overwrites(self, registry):
        registry.register("echo", "Echo v1", lambda s: s)
        registry.register("echo", "Echo v2", lambda s: s.upper())
        td = registry.get("echo")
        assert td.description == "Echo v2"

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_get_detail(self, registry_with_echo):
        detail = registry_with_echo.get_detail("echo")
        assert detail is not None
        assert detail["name"] == "echo"
        assert "input_schema" in detail

    def test_get_detail_nonexistent(self, registry):
        assert registry.get_detail("nonexistent") is None

    def test_list_tools(self, registry_with_echo):
        tools = registry_with_echo.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"

    def test_unregister(self, registry_with_echo):
        registry_with_echo.unregister("echo")
        assert registry_with_echo.get("echo") is None

    def test_unregister_nonexistent(self, registry):
        registry.unregister("nonexistent")  # should not raise

    def test_call_success(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        result = registry.call("echo", s="hello")
        assert result == "hello"

    def test_call_not_found(self, registry):
        result = registry.call("nonexistent")
        assert "not found" in result

    def test_call_disabled(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        td = registry.get("echo")
        td.enabled = False
        result = registry.call("echo", s="hello")
        assert "disabled" in result

    def test_call_exception(self, registry):
        def bad_handler(**kwargs):
            raise ValueError("boom")

        registry.register("bad", "Bad tool", bad_handler)
        result = registry.call("bad")
        assert "boom" in result

    def test_call_import_error_raises(self, registry):
        def import_error_handler(**kwargs):
            raise ImportError("missing module")

        registry.register("imp_err", "Import error tool", import_error_handler)
        with pytest.raises(ImportError):
            registry.call("imp_err")


class TestDefinitions:
    def test_anthropic_format(self, registry):
        registry.register("echo", "Echo", lambda s: s,
                          {"type": "object", "properties": {"s": {"type": "string"}}})
        defs = registry.definitions(api_format="anthropic")
        assert len(defs) == 1
        assert defs[0]["name"] == "echo"
        assert "input_schema" in defs[0]

    def test_openai_completions_format(self, registry):
        registry.register("echo", "Echo", lambda s: s,
                          {"type": "object", "properties": {"s": {"type": "string"}}})
        defs = registry.definitions(api_format="openai-completions")
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "echo"

    def test_openai_response_format(self, registry):
        registry.register("echo", "Echo", lambda s: s,
                          {"type": "object", "properties": {"s": {"type": "string"}}})
        defs = registry.definitions(api_format="openai-response")
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["name"] == "echo"
        assert "parameters" in defs[0]

    def test_disabled_excluded(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        registry.get("echo").enabled = False
        defs = registry.definitions()
        assert len(defs) == 0

    def test_active_tools_filter(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        registry.register("fetch", "Fetch", lambda u: u)
        defs = registry.definitions(active_tools={"echo"})
        assert len(defs) == 1
        # Name is nested differently per format — check by default (openai-completions)
        assert defs[0]["function"]["name"] == "echo"

    def test_active_tools_none_includes_all(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        defs = registry.definitions(active_tools=None)
        assert len(defs) == 1


class TestFormatToolsSummary:
    def test_format_unloaded(self, registry):
        registry.register("echo", "Echo tool", lambda s: s)
        registry.register("fetch", "Fetch tool", lambda u: u)
        summary = registry.format_tools_summary()
        assert "- echo: Echo tool" in summary
        assert "- fetch: Fetch tool" in summary

    def test_format_excludes_loaded(self, registry):
        registry.register("echo", "Echo tool", lambda s: s)
        registry.register("fetch", "Fetch tool", lambda u: u)
        summary = registry.format_tools_summary(loaded={"echo"})
        assert "- echo" not in summary
        assert "- fetch: Fetch tool" in summary

    def test_format_excludes_disabled(self, registry):
        registry.register("echo", "Echo tool", lambda s: s)
        registry.get("echo").enabled = False
        summary = registry.format_tools_summary()
        assert "- echo" not in summary

    def test_format_empty_registry(self, registry):
        assert registry.format_tools_summary() == ""


class TestDecodeOutput:
    def test_utf8(self):
        data = b"hello world"
        assert decode_output(data, "gbk") == "hello world"

    def test_empty(self):
        assert decode_output(b"", "gbk") == ""

    def test_fallback_encoding(self):
        # GBK-encoded Chinese that can't decode as UTF-8
        data = "中文".encode("gbk")
        result = decode_output(data, "gbk")
        assert "中文" in result


class TestCallWithTimeout:
    def test_call_timeout_returns_error(self, registry):
        import concurrent.futures

        def slow_handler(**kwargs):
            raise concurrent.futures.TimeoutError()

        registry.register("slow", "Slow tool", slow_handler)
        result = registry.call("slow")
        assert "timed out" in result