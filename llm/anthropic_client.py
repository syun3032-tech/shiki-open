"""Anthropic Claudeプロバイダー

Claude Sonnet/Opus/Haiku等に対応。
ツール呼び出し、画像入力対応。
system promptはmessagesではなくsystem=パラメータに渡す。
"""
import base64
import json
import logging

from llm.client import LLMClient
from llm.types import (
    LLMResponse, LLMConfig, ToolDefinition,
    ContentPart, ToolCall as UnifiedToolCall,
)

logger = logging.getLogger("shiki.llm.anthropic")


def _tool_definitions_to_anthropic(tools: list[ToolDefinition]) -> list[dict]:
    """ToolDefinition リスト → Anthropic tools パラメータ形式"""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters or {"type": "object", "properties": {}},
        }
        for tool in tools
    ]


class AnthropicClient(LLMClient):
    """Anthropic Claude LLMクライアント"""

    def __init__(self):
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package is required. Install with: pip install anthropic"
            )

        from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
        if not ANTHROPIC_API_KEY:
            logger.warning("ANTHROPIC_API_KEY is not set")

        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._default_model = ANTHROPIC_MODEL

    async def generate(
        self,
        config: LLMConfig,
        messages: list[dict],
    ) -> LLMResponse | None:
        model = config.model or self._default_model

        # メッセージの構築
        anthropic_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            parts_data = msg.get("parts", [])

            if role == "assistant":
                content = self._build_assistant_content(parts_data)
                anthropic_messages.append({"role": "assistant", "content": content})
            elif role == "tool_result":
                # ツール結果はuserロールで送る
                anthropic_messages.append({
                    "role": "user",
                    "content": msg.get("content", []),
                })
            else:
                # user message
                content = self._build_user_content(parts_data)
                anthropic_messages.append({"role": "user", "content": content})

        # 連続する同じroleのメッセージをマージ（Anthropic APIの制約）
        anthropic_messages = self._merge_consecutive_roles(anthropic_messages)

        # API呼び出しパラメータ
        kwargs = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": config.max_tokens,
        }

        # system promptはsystem=パラメータに渡す（messagesに入れない）
        if config.system_prompt:
            kwargs["system"] = config.system_prompt

        if config.tools:
            kwargs["tools"] = _tool_definitions_to_anthropic(config.tools)

        # temperatureが0でない場合のみ指定（Claudeのデフォルトは1.0）
        if config.temperature > 0:
            kwargs["temperature"] = config.temperature

        try:
            response = await self._client.messages.create(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            return None

    def _build_user_content(self, parts_data: list) -> list[dict]:
        """ユーザーメッセージのcontentブロックリストを構築"""
        content_blocks = []
        for p in parts_data:
            if isinstance(p, ContentPart):
                if p.image_bytes:
                    b64 = base64.b64encode(p.image_bytes).decode("utf-8")
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": p.mime_type,
                            "data": b64,
                        },
                    })
                if p.text:
                    content_blocks.append({"type": "text", "text": p.text})
            elif isinstance(p, dict):
                if "text" in p:
                    content_blocks.append({"type": "text", "text": p["text"]})
            elif isinstance(p, str):
                content_blocks.append({"type": "text", "text": p})

        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})
        return content_blocks

    def _build_assistant_content(self, parts_data: list) -> list[dict]:
        """アシスタントメッセージのcontentブロックリストを構築"""
        content_blocks = []
        for p in parts_data:
            if isinstance(p, ContentPart):
                if p.text:
                    content_blocks.append({"type": "text", "text": p.text})
                if p.tool_call:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": p.tool_call.id,
                        "name": p.tool_call.name,
                        "input": p.tool_call.args,
                    })

        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})
        return content_blocks

    def _merge_consecutive_roles(self, messages: list[dict]) -> list[dict]:
        """連続する同じroleのメッセージをマージ（Anthropic API制約対応）"""
        if not messages:
            return messages

        merged = [messages[0]]
        for msg in messages[1:]:
            if msg["role"] == merged[-1]["role"]:
                # 同じroleが連続 → contentをマージ
                prev_content = merged[-1]["content"]
                curr_content = msg["content"]
                if isinstance(prev_content, list) and isinstance(curr_content, list):
                    merged[-1]["content"] = prev_content + curr_content
                elif isinstance(prev_content, str) and isinstance(curr_content, str):
                    merged[-1]["content"] = prev_content + "\n" + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, str):
                    merged[-1]["content"] = prev_content + [{"type": "text", "text": curr_content}]
                elif isinstance(prev_content, str) and isinstance(curr_content, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev_content}] + curr_content
            else:
                merged.append(msg)
        return merged

    def _parse_response(self, response) -> LLMResponse | None:
        """Anthropicレスポンス → LLMResponse に変換"""
        parts = []
        has_tool_calls = False

        for block in response.content:
            if block.type == "text":
                if block.text:
                    parts.append(ContentPart(text=block.text))
            elif block.type == "tool_use":
                has_tool_calls = True
                parts.append(ContentPart(
                    tool_call=UnifiedToolCall(
                        name=block.name,
                        args=block.input if isinstance(block.input, dict) else {},
                        id=block.id,
                    )
                ))

        finish_reason = "tool_use" if has_tool_calls else "stop"
        if response.stop_reason == "end_turn":
            finish_reason = "stop"
        elif response.stop_reason == "tool_use":
            finish_reason = "tool_use"

        return LLMResponse(parts=parts, finish_reason=finish_reason, raw=response)

    def format_tool_result(self, tool_call_id: str, tool_name: str, result: dict) -> dict:
        """ツール結果をAnthropic形式のメッセージに変換"""
        return {
            "role": "tool_result",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            ],
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
