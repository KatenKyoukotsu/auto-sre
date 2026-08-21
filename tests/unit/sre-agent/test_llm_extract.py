"""Юнит-тесты extract_json из sre-agent/llm.py (устойчивость к капризам малых LLM)."""
import llm


class TestExtractJson:
    def test_plain_object(self):
        assert llm.extract_json('{"a": 1}') == {"a": 1}

    def test_wrapped_in_prose_and_code_fence(self):
        text = 'Вот результат анализа:\n```json\n{"severity": "critical", "confidence": 0.9}\n```\nЧто-то ещё.'
        assert llm.extract_json(text) == {"severity": "critical", "confidence": 0.9}

    def test_trailing_commas_tolerated(self):
        assert llm.extract_json('{"a": 1,}') == {"a": 1}
        assert llm.extract_json('{"a": [1, 2,], "b": {"c": 3,},}') == {"a": [1, 2], "b": {"c": 3}}

    def test_nested_braces(self):
        assert llm.extract_json('преамбула {"a": {"b": [1, {"c": 2}]}} эпилог') == {"a": {"b": [1, {"c": 2}]}}

    def test_garbage_returns_empty_dict(self):
        assert llm.extract_json("Модель написала просто текст без JSON") == {}

    def test_non_dict_json_returns_empty_dict(self):
        # regex \{.*\} не матчит массив — и это ок
        assert llm.extract_json("[1, 2, 3]") == {}

    def test_unfixable_broken_json_returns_empty_dict(self):
        assert llm.extract_json('{"a": 1,,, }') == {}

    def test_first_brace_to_last_greedily(self):
        # жадный regex: несколько объектов склеиваются в невалид -> {}, а не падение
        result = llm.extract_json('{"a": 1} мусор {"b": 2}')
        assert isinstance(result, dict)
