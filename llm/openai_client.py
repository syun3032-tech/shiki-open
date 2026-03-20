"""OpenAI GPTプロバイダー

GPT-4o, GPT-4-turbo等に対応。
ツール呼び出し、画像入力、ストリーミング対応。
"""
import base64
import json
import logging

from llm.client import LLMClient
from llm.types import (
    LLMResponse, LLMConfig, ToolDefinition,
    ContentPart, ToolCall as UnifiedToolCall,
)

logger = logging.getLogger("shiki.llm.openai")


def _tool_definitions_to_openai(tools: list[ToolDefinition]) -> list[dict]:
    """ToolDefinition リスト → OpenAI tools パラメータ形式"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


class OpenAIClient(LLMClient):
    """OpenAI GPT LLMクライアント"""

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package is required. Install with: pip install openai"
            )

        if api_key is None:
            from config import OPENAI_API_KEY
            api_key = OPENAI_API_KEY
        if not api_key and base_url is None:
            logger.warning("OPENAI_API_KEY is not set")

        from config import OPENAI_MODEL
        self._default_model = OPENAI_MODEL

        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key

        self._client = openai.AsyncOpenAI(**kwargs)

    async def generate(
        self,
        config: LLMConfig,
        messages: list[dict],
    ) -> LLMResponse | None:
        model = config.model or self._default_model

        # メッセージの構築
        openai_messages = []

        # System prompt
        if config.system_prompt:
            openai_messages.append({
                "role": "system",
                "content": config.system_prompt,
            })

        for msg in messages:
            role = msg.get("role", "user")
            parts_data = msg.get("parts", [])

            # tool result (特殊処理)
            if role == "tool":
                openai_messages.append(msg)
                continue

            # 通常メッセージ
            if role == "assistant":
                # アシスタントメッセージ（tool_calls含む可能性）
                assistant_msg = self._build_assistant_message(parts_data)
                openai_messages.append(assistant_msg)
            else:
                # ユーザーメッセージ
                content = self._build_user_content(parts_data)
                openai_messages.append({"role": "user", "content": content})

        # API呼び出しパラメータ
        kwargs = {
            "model": model,
            "messages": openai_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if config.tools:
            kwargs["tools"] = _tool_definitions_to_openai(config.tools)

        try:
            response = await self._client.chat.completions.create(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return None

    def _build_user_content(self, parts_data: list) -> list[dict] | str:
        """ユーザーメッセージのcontentを構築"""
        content_parts = []
        for p in parts_data:
            if isinstance(p, ContentPart):
                if p.image_bytes:
                    b64 = base64.b64encode(p.image_bytes).decode("utf-8")
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{p.mime_type};base64,{b64}",
                        },
                    })
                if p.text:
                    content_parts.append({"type": "text", "text": p.text})
            elif isinstance(p, dict):
                if "text" in p:
                    content_parts.append({"type": "text", "text": p["text"]})
            elif isinstance(p, str):
                content_parts.append({"type": "text", "text": p})

        # 単一テキストの場合は文字列に簡略化
        if len(content_parts) == 1 and content_parts[0].get("type") == "text":
            return content_parts[0]["text"]
        return content_parts

    def _build_assistant_message(self, parts_data: list) -> dict:
        """アシスタントメッセージを構築（tool_calls含む）"""
        text_parts = []
        tool_calls = []

        for p in parts_data:
            if isinstance(p, ContentPart):
                if p.text:
                    text_parts.append(p.text)
                if p.tool_call:
                    tool_calls.append({
                        "id": p.tool_call.id,
                        "type": "function",
                        "function": {
                            "name": p.tool_call.name,
                            "arguments": json.dumps(p.tool_call.args, ensure_ascii=False),
                        },
                    })

        msg: dict = {"role": "assistant"}
        if text_parts:
            msg["content"] = "\n".join(text_parts)
        else:
            msg["content"] = None
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    def _parse_response(self, response) -> LLMResponse | None:
        """OpenAIレスポンス → LLMResponse に変換"""
        if not response.choices:
            logger.warning("Empty choices from OpenAI")
            return None

        choice = response.choices[0]
        message = choice.message
        parts = []
        has_tool_calls = False

        # テキスト
        if message.content:
            parts.append(ContentPart(text=message.content))

        # ツール呼び出し
        if message.tool_calls:
            has_tool_calls = True
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                parts.append(ContentPart(
                    tool_call=UnifiedToolCall(
                        name=tc.function.name,
                        args=args,
                        id=tc.id,
                    )
                ))

        finish_reason = "tool_use" if has_tool_calls else "stop"
        if choice.finish_reason == "content_filter":
            finish_reason = "safety"

        return LLMResponse(parts=parts, finish_reason=finish_reason, raw=response)

    def format_tool_result(self, tool_call_id: str, tool_name: str, result: dict) -> dict:
        """ツール結果をOpenAI形式のメッセージに変換"""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result, ensure_ascii=False, default=str),
        }

    def format_user_message(self, text: str, image_bytes: bytes | None = None) -> dict:
        """ユーザーメッセージをフォーマット"""
        parts = []
        if image_bytes:
            parts.append(ContentPart(image_bytes=image_bytes, mime_type="image/jpeg"))
        parts.append(ContentPart(text=text))
        return {"role": "user", "parts": parts}

    def format_assistant_message(self, parts: list[ContentPart]) -> dict:
        """アシスタントメッセージをフォーマット"""
        return {"role": "assistant", "parts": parts}
