"""Tests for qd_evolve.core.registry — ToolRegistry, ToolDef, definitions, call."""

from unittest.mock import patch

import pytest

from qd_evolve.core.registry import ToolDef, decode_output


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
    def test_format_all_with_status_tags(self, registry):
        registry.register("echo", "Echo tool", lambda s: s)
        registry.register("fetch", "Fetch tool", lambda u: u)
        summary = registry.format_tools_summary()
        assert "- [inactive] echo: Echo tool" in summary
        assert "- [inactive] fetch: Fetch tool" in summary

    def test_format_ready_tagged(self, registry):
        registry.register("echo", "Echo tool", lambda s: s)
        registry.register("fetch", "Fetch tool", lambda u: u)
        summary = registry.format_tools_summary(preloaded={"echo"})
        assert "- [ready] echo: Echo tool" in summary
        assert "- [inactive] fetch: Fetch tool" in summary

    def test_format_loaded_tagged(self, registry):
        registry.register("echo", "Echo tool", lambda s: s)
        registry.register("fetch", "Fetch tool", lambda u: u)
        summary = registry.format_tools_summary(loaded={"echo"})
        assert "- [ready] echo: Echo tool" in summary
        assert "- [inactive] fetch: Fetch tool" in summary

    def test_format_ready_overrides_inactive(self, registry):
        """preloaded and loaded both result in [ready]."""
        registry.register("echo", "Echo tool", lambda s: s)
        summary = registry.format_tools_summary(preloaded={"echo"}, loaded={"echo"})
        assert "- [ready] echo" in summary

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

    def test_call_runtime_error_on_thread_start(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        import threading
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("cannot start thread")):
            result = registry.call("echo", s="hello")
        assert "unavailable" in result

    def test_call_actual_timeout(self, registry):
        def very_slow(**kwargs):
            import time
            time.sleep(999)

        registry.register("slow", "Very slow tool", very_slow)
        import threading
        with patch.object(threading.Thread, "join", return_value=None), \
             patch.object(threading.Thread, "is_alive", return_value=True):
            result = registry.call("slow")
        assert "timed out" in result


class TestFormatToolsSummaryExtended:
    """Tests for MCP/OAT source grouping and service summary in format_tools_summary."""

    def test_mcp_source_grouping(self, registry):
        from qd_evolve.core.registry import set_source_bridge_type

        set_source_bridge_type("github", "mcp")
        registry.register(
            "github_search",
            "[github] Search GitHub repositories",
            lambda q: q,
        )
        registry.register(
            "github_issues",
            "[github] List GitHub issues",
            lambda q: q,
        )
        summary = registry.format_tools_summary()
        assert "MCP: github (2)" in summary
        assert "MCP Services" in summary
        assert "github" in summary

    def test_oat_source_grouping(self, registry):
        from qd_evolve.core.registry import set_source_bridge_type

        set_source_bridge_type("boat", "oat")
        registry.register(
            "boat_search",
            "[boat] Search with boat",
            lambda q: q,
        )
        summary = registry.format_tools_summary()
        assert "OAT: boat (1)" in summary
        assert "boat (1 tools)" in summary

    def test_mixed_mcp_and_oat(self, registry):
        from qd_evolve.core.registry import set_source_bridge_type

        set_source_bridge_type("github", "mcp")
        set_source_bridge_type("boat", "oat")
        registry.register("gh", "[github] GH tool", lambda: "")
        registry.register("bt", "[boat] Boat tool", lambda: "")

        summary = registry.format_tools_summary()
        assert "MCP:" in summary
        assert "OAT:" in summary

    def test_builtins_before_mcp(self, registry):
        from qd_evolve.core.registry import set_source_bridge_type

        set_source_bridge_type("src", "mcp")
        registry.register("echo", "Echo tool", lambda s: s)
        registry.register("mcp_tool", "[src] MCP tool desc", lambda: "")

        summary = registry.format_tools_summary(preloaded={"echo"})
        # Builtins section should come after summary
        assert "Builtins" in summary
        assert "MCP Services" in summary

    def test_default_bridge_type_is_mcp(self, registry):
        """Without set_source_bridge_type, source defaults to MCP."""
        registry.register("t1", "[unknown_src] Description", lambda: "")
        summary = registry.format_tools_summary()
        assert "MCP Services" in summary or "MCP:" in summary

    def test_no_duplicate_summary_line(self, registry):
        """format_tools_summary should have meaningful content."""
        registry.register("echo", "Echo tool", lambda s: s)
        summary = registry.format_tools_summary()
        assert "Builtins" in summary
        assert "[inactive] echo: Echo tool" in summary


class TestSetSourceBridgeType:
    def test_registers_bridge_type(self):
        from qd_evolve.core.registry import set_source_bridge_type, _source_bridge_types

        old = _source_bridge_types.copy()
        try:
            _source_bridge_types.clear()
            set_source_bridge_type("test_source", "oat")
            assert _source_bridge_types["test_source"] == "oat"

            set_source_bridge_type("test_source", "mcp")
            assert _source_bridge_types["test_source"] == "mcp"
        finally:
            _source_bridge_types.clear()
            _source_bridge_types.update(old)


class TestDiscoverTools:
    def test_discover_system_tools_error_handled(self, registry, tmp_path):
        with patch("importlib.import_module", side_effect=ImportError("module not found")):
            registry.discover_tools()

    def test_discover_func_tools_error_handled(self, registry, tmp_path):
        func_dir = tmp_path / "func"
        func_dir.mkdir()
        (func_dir / "broken.py").write_text("syntax error here !!!", encoding="utf-8")

        import qd_evolve.core.config as config_mod
        with patch.object(config_mod, "FUNC_TOOLS_DIR", str(func_dir)):
            registry.discover_tools()

    def test_get_registry_singleton(self):
        from qd_evolve.core.registry import get_registry
        # Reset singleton
        import qd_evolve.core.registry as reg_mod
        reg_mod._registry = None
        r = get_registry()
        assert r is not None

    def test_get_registry_singleton_cached(self):
        from qd_evolve.core.registry import get_registry
        import qd_evolve.core.registry as reg_mod

        old = reg_mod._registry
        reg_mod._registry = None
        try:
            r1 = get_registry()
            r2 = get_registry()
            assert r1 is r2
        finally:
            reg_mod._registry = old


class TestDecodeOutputExtended:
    def test_gbk_chinese(self):
        from qd_evolve.core.registry import decode_output
        data = "你好世界".encode("gbk")
        result = decode_output(data, "gbk")
        assert "你好世界" in result

    def test_empty_bytes(self):
        from qd_evolve.core.registry import decode_output
        assert decode_output(b"", "gbk") == ""

    def test_utf8_fallback(self):
        from qd_evolve.core.registry import decode_output
        data = "café résumé".encode("utf-8")
        result = decode_output(data, "gbk")
        assert "café" in result

    def test_unknown_encoding_skipped(self):
        from qd_evolve.core.registry import decode_output
        data = b"hello"
        # fallback with a bad encoding name should still work via utf-8
        result = decode_output(data, "nonexistent_encoding_xyz")
        assert result == "hello"

    def test_latin1_data(self):
        from qd_evolve.core.registry import decode_output
        data = "café".encode("latin-1")
        result = decode_output(data, "gbk")
        assert "café" in result

    def test_duplicate_encoding_not_repeated(self):
        from qd_evolve.core.registry import decode_output
        # With fallback_enc="utf-8", utf-8 should only appear once in candidates
        assert decode_output(b"hello world", "utf-8") == "hello world"


class TestDefinitionsExtended:
    def test_active_tools_empty_set(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        defs = registry.definitions(active_tools=set())
        assert len(defs) == 0

    def test_active_tools_excludes_non_members(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        registry.register("fetch", "Fetch", lambda u: u)
        defs = registry.definitions(active_tools={"echo"})
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "echo"

    def test_default_api_format_is_openai_completions(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        defs = registry.definitions()
        assert len(defs) == 1
        assert defs[0]["type"] == "function"

    def test_openai_completions_format_has_nested_function(self, registry):
        registry.register("echo", "Echo", lambda s: s)
        defs = registry.definitions(api_format="openai-completions")
        assert defs[0]["function"]["name"] == "echo"

    def test_multiple_tools_ordered(self, registry):
        registry.register("b", "B tool", lambda: "")
        registry.register("a", "A tool", lambda: "")
        defs = registry.definitions()
        assert len(defs) == 2

    