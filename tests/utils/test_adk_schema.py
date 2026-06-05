"""Tests for qd_evolve.utils.adk_schema — Google ADK to OpenAI JSON Schema converter.

Covers edge cases for _annotation_to_json_type and google_adk_to_openai_schema.
"""

import inspect
import types
from typing import Any, Optional, Union, List, Dict

import pytest

from qd_evolve.utils.adk_schema import _annotation_to_json_type, google_adk_to_openai_schema


class TestAnnotationToJsonType:
    def test_str_annotation(self):
        assert _annotation_to_json_type(str) == {"type": "string"}

    def test_int_annotation(self):
        assert _annotation_to_json_type(int) == {"type": "integer"}

    def test_float_annotation(self):
        assert _annotation_to_json_type(float) == {"type": "number"}

    def test_bool_annotation(self):
        assert _annotation_to_json_type(bool) == {"type": "boolean"}

    def test_any_annotation(self):
        assert _annotation_to_json_type(Any) == {}

    def test_union_with_none(self):
        # typing.Union[str, None]
        schema = _annotation_to_json_type(Union[str, None])
        assert schema["type"] == "string"
        assert schema.get("nullable") is True

    def test_union_simple(self):
        # typing.Union[int, str]
        schema = _annotation_to_json_type(Union[int, str])
        assert schema["type"] == "integer"

    def test_union_all_none(self):
        # typing.Union[None, None] — pathological but shouldn't crash
        schema = _annotation_to_json_type(Union[None, None])
        assert isinstance(schema, dict)

    def test_list_annotation_with_args(self):
        schema = _annotation_to_json_type(List[str])
        assert schema["type"] == "array"
        assert schema["items"] == {"type": "string"}

    def test_list_annotation_no_args(self):
        # Bare list (not subscripted) — no origin, falls through to empty schema
        schema = _annotation_to_json_type(list)
        # list class has no typing origin when not subscripted
        assert isinstance(schema, dict)

    def test_dict_annotation_with_args(self):
        schema = _annotation_to_json_type(Dict[str, int])
        assert schema["type"] == "object"
        assert schema["additionalProperties"] == {"type": "integer"}

    def test_dict_annotation_no_args(self):
        # Bare dict (not subscripted) — no origin, falls through to empty schema
        schema = _annotation_to_json_type(dict)
        assert isinstance(schema, dict)

    def test_optional_annotation(self):
        schema = _annotation_to_json_type(Optional[str])
        assert schema["type"] == "string"
        assert schema.get("nullable") is True

    def test_optional_non_none(self):
        schema = _annotation_to_json_type(Optional[int])
        assert schema["type"] == "integer"
        assert schema.get("nullable") is True

    def test_named_type_by_str_name(self):
        """When annotation name resolves by string comparison."""

        class MyCustom:
            pass

        schema = _annotation_to_json_type(MyCustom)
        # Should return empty dict for unknown types
        assert schema == {}

    def test_pep604_union_type(self):
        """Test Python 3.10+ union syntax (str | None)."""
        schema = _annotation_to_json_type(str | None)
        assert schema["type"] == "string"
        assert schema.get("nullable") is True

    def test_pep604_union_without_none(self):
        schema = _annotation_to_json_type(int | str)
        assert schema["type"] == "integer"

    def test_str_name_match(self):
        """Annotation with __name__ == 'str'."""
        ann = str
        assert _annotation_to_json_type(ann) == {"type": "string"}

    def test_int_name_match(self):
        ann = int
        assert _annotation_to_json_type(ann) == {"type": "integer"}

    def test_float_name_match(self):
        ann = float
        assert _annotation_to_json_type(ann) == {"type": "number"}

    def test_bool_name_match(self):
        ann = bool
        assert _annotation_to_json_type(ann) == {"type": "boolean"}


class TestGoogleAdkToOpenaiSchema:
    def test_simple_function(self):
        def fn(name: str, age: int) -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert schema["type"] == "object"
        assert schema["properties"]["name"] == {"type": "string"}
        assert schema["properties"]["age"] == {"type": "integer"}
        assert "name" in schema["required"]
        assert "age" in schema["required"]

    def test_strips_skip_confirm(self):
        def fn(query: str, skip_confirm: bool = False) -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert "skip_confirm" not in schema["properties"]
        assert "skip_confirm" not in schema["required"]
        assert "query" in schema["properties"]

    def test_optional_parameter(self):
        def fn(name: str, description: str = "") -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert "name" in schema["required"]
        assert "description" not in schema["required"]

    def test_no_annotations(self):
        def fn(x, y):
            pass

        schema = google_adk_to_openai_schema(fn)
        assert schema["type"] == "object"

    def test_mixed_types(self):
        def fn(text: str, count: int, active: bool, score: float) -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert schema["properties"]["text"] == {"type": "string"}
        assert schema["properties"]["count"] == {"type": "integer"}
        assert schema["properties"]["active"] == {"type": "boolean"}
        assert schema["properties"]["score"] == {"type": "number"}

    def test_list_and_dict_params(self):
        # Use typing.List/Dict subscripted for proper origin handling
        def fn(items: List[str], mapping: Dict[str, int]) -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert schema["properties"]["items"]["type"] == "array"
        assert schema["properties"]["mapping"]["type"] == "object"

    def test_no_params(self):
        def fn() -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert schema["required"] == []

    def test_all_optional_params(self):
        def fn(a: str = "a", b: int = 1) -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert schema["required"] == []

    def test_union_type_param(self):
        def fn(value: Union[str, int]) -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert "value" in schema["properties"]
        assert "value" in schema["required"]

    def test_pep604_optional_param(self):
        def fn(name: "str | None") -> None:
            pass

        schema = google_adk_to_openai_schema(fn)
        assert "name" in schema["properties"]

    def test_lambda_function(self):
        fn = lambda s: s  # noqa: E731
        schema = google_adk_to_openai_schema(fn)
        assert schema["type"] == "object"
