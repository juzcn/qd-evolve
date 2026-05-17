"""Tests for qd_evolve.utils.adk_schema — Google ADK to OpenAI JSON Schema conversion."""

import types
import typing
from typing import Any, Optional

import pytest

from qd_evolve.utils.adk_schema import google_adk_to_openai_schema, _annotation_to_json_type


class TestAnnotationToJsonType:
    def test_str(self):
        assert _annotation_to_json_type(str) == {"type": "string"}

    def test_int(self):
        assert _annotation_to_json_type(int) == {"type": "integer"}

    def test_float(self):
        assert _annotation_to_json_type(float) == {"type": "number"}

    def test_bool(self):
        assert _annotation_to_json_type(bool) == {"type": "boolean"}

    def test_any(self):
        assert _annotation_to_json_type(Any) == {}

    def test_list_str(self):
        result = _annotation_to_json_type(list[str])
        assert result["type"] == "array"
        assert result["items"]["type"] == "string"

    def test_list_int(self):
        result = _annotation_to_json_type(list[int])
        assert result["type"] == "array"
        assert result["items"]["type"] == "integer"

    def test_dict_str_str(self):
        result = _annotation_to_json_type(dict[str, str])
        assert result["type"] == "object"
        assert result["additionalProperties"]["type"] == "string"

    def test_dict_str_int(self):
        result = _annotation_to_json_type(dict[str, int])
        assert result["type"] == "object"
        assert result["additionalProperties"]["type"] == "integer"

    def test_optional_str(self):
        result = _annotation_to_json_type(Optional[str])
        assert result["type"] == "string"
        assert result.get("nullable") is True

    def test_union_str_none(self):
        result = _annotation_to_json_type(str | None)
        assert result["type"] == "string"
        assert result.get("nullable") is True

    def test_list_empty(self):
        # Bare `list` without type args — falls through to unknown
        result = _annotation_to_json_type(list)
        assert result == {}

    def test_dict_empty(self):
        # Bare `dict` without type args — falls through to unknown
        result = _annotation_to_json_type(dict)
        assert result == {}

    def test_unknown_type(self):
        result = _annotation_to_json_type(object)
        assert result == {}


class TestGoogleAdkToOpenaiSchema:
    def test_simple_function(self):
        def my_tool(query: str, limit: int) -> str:
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"
        assert schema["properties"]["limit"]["type"] == "integer"
        assert "query" in schema["required"]
        assert "limit" in schema["required"]

    def test_optional_parameter(self):
        def my_tool(query: str, limit: int = 5) -> str:
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert "query" in schema["required"]
        assert "limit" not in schema["required"]

    def test_skip_confirm_stripped(self):
        def my_tool(query: str, skip_confirm: bool = False) -> str:
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert "skip_confirm" not in schema["properties"]
        assert "query" in schema["properties"]

    def test_no_annotations(self):
        def my_tool(query, limit) -> str:
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "limit" in schema["properties"]
        # Both required since no defaults
        assert "query" in schema["required"]

    def test_list_parameter(self):
        def my_tool(items: list[str]) -> str:
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["properties"]["items"]["type"] == "array"
        assert schema["properties"]["items"]["items"]["type"] == "string"

    def test_dict_parameter(self):
        def my_tool(config: dict[str, str]) -> str:
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["properties"]["config"]["type"] == "object"

    def test_optional_parameter_nullable(self):
        def my_tool(query: str, filter: Optional[str] = None) -> str:
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert "filter" not in schema["required"]
        assert schema["properties"]["filter"].get("nullable") is True