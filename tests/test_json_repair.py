"""Tests for JSON repair utility."""
from src.utils.json_repair import parse_json_response


def test_parse_clean_json():
    result = parse_json_response('[{"title": "test"}]')
    assert result == [{"title": "test"}]


def test_parse_code_block():
    result = parse_json_response('```json\n[{"a": 1}]\n```')
    assert result == [{"a": 1}]


def test_parse_with_surrounding_text():
    result = parse_json_response('Here is the result:\n{"key": "value"}\nDone!')
    assert result == {"key": "value"}


def test_parse_truncated_array():
    result = parse_json_response('[{"a": 1}, {"b": 2}')
    assert result is not None
    assert len(result) >= 1


def test_parse_latex_escapes():
    # LaTeX \frac inside JSON string
    raw = '{"latex": "\\frac{a}{b}"}'
    result = parse_json_response(raw)
    assert result is not None


def test_parse_returns_none_for_garbage():
    result = parse_json_response("this is not json at all")
    assert result is None
