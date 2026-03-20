"""LLMクライアント — プロバイダー抽象化

使い方:
    client = get_client()  # config.pyのLLM_PROVIDER設定に基づく
    response = await client.generate(config, messages)
"""
import logging
from abc import ABC, abstractmethod
from llm.types import LLMResponse, LLMConfig, ToolDefinition, ContentPart

logger = logging.getLogger("shiki.llm")


class LLMClient(ABC):
    """LLMプロバイダーの抽象基底クラス"""

    @abstractmethod
    async def generate(
        self,
        config: LLMConfig,
        messages: list[dict],  # [{"role": "user"/"assistant", "parts": [ContentPart]}]
    ) -> LLMResponse | None:
        """Generate a response. Returns None on failure."""
        ...

    @abstractmethod
    def format_tool_result(self, tool_call_id: str, tool_name: str, result: dict) -> dict:
        """Format a tool execution result for the next turn."""
        ...

    @abstractmethod
    def format_user_message(self, text: str, image_bytes: bytes | None = None) -> dict:
        """Format a user message (optionally with image)."""
        ...

    @abstractmethod
    def format_assistant_message(self, parts: list[ContentPart]) -> dict:
        """Format an assistant response for history."""
        ...


_client_cache: LLMClient | None = None


def get_client(provider: str | None = None) -> LLMClient:
    global _client_cache
    if _client_cache is not None and provider is None:
        return _client_cache

    if provider is None:
        from config import LLM_PROVIDER
        provider = LLM_PROVIDER

    if provider == "gemini":
        from llm.gemini import GeminiClient
        _client_cache = GeminiClient()
    elif provider == "openai":
        from llm.openai_client import OpenAIClient
        _client_cache = OpenAIClient()
    elif provider == "anthropic":
        from llm.anthropic_client import AnthropicClient
        _client_cache = AnthropicClient()
    elif provider == "ollama":
        from llm.ollama_client import OllamaClient
        _client_cache = OllamaClient()
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    logger.info(f"LLM client initialized: {provider}")
    return _client_cache
