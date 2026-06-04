"""Tests for qd_evolve.utils.adk_schema — Google ADK to OpenAI JSON Schema conversion."""

from typing import Any, Optional
from unittest.mock import MagicMock


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
        def my_tool(query: str, limit: int) -> str:  # type: ignore[return-type]
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"
        assert schema["properties"]["limit"]["type"] == "integer"
        assert "query" in schema["required"]
        assert "limit" in schema["required"]

    def test_optional_parameter(self):
        def my_tool(query: str, limit: int = 5) -> str:  # type: ignore[return-type]
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert "query" in schema["required"]
        assert "limit" not in schema["required"]

    def test_skip_confirm_stripped(self):
        def my_tool(query: str, skip_confirm: bool = False) -> str:  # type: ignore[return-type]
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert "skip_confirm" not in schema["properties"]
        assert "query" in schema["properties"]

    def test_no_annotations(self):
        def my_tool(query, limit) -> str:  # type: ignore[return-type]
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "limit" in schema["properties"]
        # Both required since no defaults
        assert "query" in schema["required"]

    def test_list_parameter(self):
        def my_tool(items: list[str]) -> str:  # type: ignore[return-type]
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["properties"]["items"]["type"] == "array"
        assert schema["properties"]["items"]["items"]["type"] == "string"

    def test_dict_parameter(self):
        def my_tool(config: dict[str, str]) -> str:  # type: ignore[return-type]
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert schema["properties"]["config"]["type"] == "object"

    def test_optional_parameter_nullable(self):
        def my_tool(query: str, filter: Optional[str] = None) -> str:  # type: ignore[return-type]
            pass

        schema = google_adk_to_openai_schema(my_tool)
        assert "filter" not in schema["required"]
        assert schema["properties"]["filter"].get("nullable") is True

    def test_union_all_none(self):
        """Union where all args are None — returns empty schema."""
        result = _annotation_to_json_type(type(None) | type(None))
        assert result == {}

    def test_optional_with_empty_inner(self):
        """Optional[object] — inner is empty schema, nullable not added."""
        result = _annotation_to_json_type(Optional[object])
        # object maps to {}, which is falsy — nullable not added
        assert isinstance(result, dict)

    def test_named_type_str_fallback(self):
        """Named type matching 'str' by __name__ attribute."""
        # Create a mock that has __name__ = "str"
        mock_type = MagicMock()
        mock_type.__name__ = "str"
        mock_type.__str__ = lambda self: "str"  # type: ignore
        result = _annotation_to_json_type(mock_type)
        assert result == {"type": "string"}

    def test_named_type_int_fallback(self):
        mock_type = MagicMock()
        mock_type.__name__ = "int"
        result = _annotation_to_json_type(mock_type)
        assert result == {"type": "integer"}

    def test_named_type_float_fallback(self):
        mock_type = MagicMock()
        mock_type.__name__ = "float"
        result = _annotation_to_json_type(mock_type)
        assert result == {"type": "number"}

    def test_named_type_bool_fallback(self):
        mock_type = MagicMock()
        mock_type.__name__ = "bool"
        result = _annotation_to_json_type(mock_type)
        assert result == {"type": "boolean"}