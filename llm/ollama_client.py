"""Ollamaプロバイダー — ローカルLLM (OpenAI互換API)

llama3.1, mistral, gemma等のローカルモデルに対応。
OpenAI互換APIを使用するため、OpenAIClientを継承して
クライアント初期化のみ変更する。
"""
import logging

from llm.openai_client import OpenAIClient

logger = logging.getLogger("shiki.llm.ollama")


class OllamaClient(OpenAIClient):
    """Ollama (ローカルLLM) クライアント

    OpenAI互換APIを使用するため、OpenAIClientを継承。
    base_urlとデフォルトモデルのみ変更。
    """

    def __init__(self):
        from config import OLLAMA_BASE_URL, OLLAMA_MODEL

        # OpenAIClientの__init__をバイパスして直接設定
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package is required for Ollama client. "
                "Install with: pip install openai"
            )

        self._client = openai.AsyncOpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",  # Ollamaはキー不要だがOpenAIクライアントが要求する
        )
        self._default_model = OLLAMA_MODEL
        logger.info(f"Ollama client initialized: {OLLAMA_BASE_URL}, model={OLLAMA_MODEL}")
