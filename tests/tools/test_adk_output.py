"""Tests for qd_evolve.utils.adk_output — normalize_output, _normalize_dict, make_handler."""

import json


from qd_evolve.utils.adk_output import normalize_output, _normalize_dict, make_handler


class TestNormalizeOutput:
    def test_none(self):
        assert normalize_output(None) == "(done)"

    def test_string(self):
        assert normalize_output("hello") == "hello"

    def test_int(self):
        assert normalize_output(42) == "42"

    def test_float(self):
        assert normalize_output(3.14) == "3.14"

    def test_bool(self):
        assert normalize_output(True) == "True"

    def test_dict(self):
        result = normalize_output({"key": "value", "num": 42})
        data = json.loads(result)
        assert data["key"] == "value"
        assert data["num"] == "42"

    def test_list(self):
        result = normalize_output([1, 2, 3])
        data = json.loads(result)
        assert data == [1, 2, 3]

    def test_list_of_dicts(self):
        result = normalize_output([{"a": 1}, {"b": 2}])
        data = json.loads(result)
        assert len(data) == 2

    def test_unknown_type(self):
        class Custom:
            def __str__(self):
                return "custom"

        assert normalize_output(Custom()) == "custom"


class TestNormalizeDict:
    def test_simple(self):
        result = _normalize_dict({"key": "value"})
        assert result["key"] == "value"

    def test_none_to_empty_string(self):
        result = _normalize_dict({"key": None})
        assert result["key"] == ""

    def test_int_to_string(self):
        result = _normalize_dict({"key": 42})
        assert result["key"] == "42"

    def test_nested_dict(self):
        result = _normalize_dict({"outer": {"inner": 42}})
        assert result["outer"]["inner"] == "42"

    def test_nested_list(self):
        result = _normalize_dict({"items": [1, 2, 3]})
        assert result["items"] == ["1", "2", "3"]

    def test_nested_list_of_dicts(self):
        result = _normalize_dict({"items": [{"a": 1}, {"b": 2}]})
        assert result["items"][0]["a"] == "1"


class TestMakeHandler:
    def test_wraps_function(self):
        def my_tool(query: str) -> str:
            return f"result: {query}"

        handler = make_handler(my_tool)
        result = handler(query="hello")
        assert result == "result: hello"

    def test_normalizes_dict_output(self):
        def my_tool() -> dict:
            return {"key": "value"}

        handler = make_handler(my_tool)
        result = handler()
        data = json.loads(result)
        assert data["key"] == "value"

    def test_injects_skip_confirm(self):
        def my_tool(query: str, skip_confirm: bool = False) -> str:
            return f"confirmed={skip_confirm}"

        handler = make_handler(my_tool)
        result = handler(query="hello")
        assert "confirmed=True" in result

    def test_exception_returns_error(self):
        def my_tool() -> str:
            raise ValueError("boom")

        handler = make_handler(my_tool)
        result = handler()
        data = json.loads(result)
        assert "error" in data
        assert "boom" in data["error"]

    def test_none_output(self):
        def my_tool() -> None:
            pass

        handler = make_handler(my_tool)
        result = handler()
        assert result == "(done)"