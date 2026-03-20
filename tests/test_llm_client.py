"""llm/client.py のテスト — get_clientファクトリ"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import llm.client as client_module


@pytest.fixture(autouse=True)
def reset_client_cache():
    """各テストの前にキャッシュをクリア"""
    client_module._client_cache = None
    yield
    client_module._client_cache = None


class TestGetClient:
    """get_clientファクトリのテスト"""

    def test_gemini_returns_gemini_client(self):
        with patch("llm.gemini.GeminiClient") as MockClass:
            MockClass.return_value = MagicMock()
            c = client_module.get_client("gemini")
            MockClass.assert_called_once()
            assert c is MockClass.return_value

    def test_openai_returns_openai_client(self):
        with patch("llm.openai_client.OpenAIClient") as MockClass:
            MockClass.return_value = MagicMock()
            c = client_module.get_client("openai")
            MockClass.assert_called_once()
            assert c is MockClass.return_value

    def test_anthropic_returns_anthropic_client(self):
        with patch("llm.anthropic_client.AnthropicClient") as MockClass:
            MockClass.return_value = MagicMock()
            c = client_module.get_client("anthropic")
            MockClass.assert_called_once()
            assert c is MockClass.return_value

    def test_ollama_returns_ollama_client(self):
        with patch("llm.ollama_client.OllamaClient") as MockClass:
            MockClass.return_value = MagicMock()
            c = client_module.get_client("ollama")
            MockClass.assert_called_once()
            assert c is MockClass.return_value

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            client_module.get_client("unknown")

    def test_client_caching_works(self):
        """provider=None の2回目呼び出しはキャッシュを返す"""
        mock_client = MagicMock()
        client_module._client_cache = mock_client

        result = client_module.get_client(None)
        assert result is mock_client

    def test_explicit_provider_bypasses_cache(self):
        """providerを明示指定するとキャッシュを上書き"""
        old_mock = MagicMock()
        client_module._client_cache = old_mock

        with patch("llm.gemini.GeminiClient") as MockClass:
            MockClass.return_value = MagicMock()
            c = client_module.get_client("gemini")
            assert c is not old_mock
            assert c is MockClass.return_value
