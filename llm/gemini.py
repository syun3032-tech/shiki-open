"""Geminiプロバイダー — google.genai SDKラッパー

既存のloop.py/tools_config.pyとの後方互換を維持しつつ、
LLMClient抽象に準拠する。
"""
import asyncio
import json
import logging

import google.genai as genai

from llm.client import LLMClient
from llm.types import (
    LLMResponse, LLMConfig, ToolDefinition,
    ContentPart, ToolCall as UnifiedToolCall,
)

logger = logging.getLogger("shiki.llm.gemini")

# JSON Schema type → Gemini Schema type マッピング
_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _json_schema_to_genai_schema(schema: dict) -> genai.types.Schema:
    """JSON Schema (OpenAI形式) → genai.types.Schema に変換"""
    schema_type = _TYPE_MAP.get(schema.get("type", "string"), "STRING")

    kwargs: dict = {"type": schema_type}

    if "description" in schema:
        kwargs["description"] = schema["description"]

    if "enum" in schema:
        kwargs["enum"] = schema["enum"]

    # Object properties
    if schema_type == "OBJECT" and "properties" in schema:
        kwargs["properties"] = {
            k: _json_schema_to_genai_schema(v)
            for k, v in schema["properties"].items()
        }
        if "required" in schema:
            kwargs["required"] = schema["required"]

    # Array items
    if schema_type == "ARRAY" and "items" in schema:
        kwargs["items"] = _json_schema_to_genai_schema(schema["items"])

    return genai.types.Schema(**kwargs)


def _tool_definitions_to_gemini(tools: list[ToolDefinition]) -> genai.types.Tool:
    """ToolDefinition リスト → Gemini Tool オブジェクトに変換"""
    declarations = []
    for tool in tools:
        params = tool.parameters if tool.parameters else {"type": "object", "properties": {}}
        declarations.append(
            genai.types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=_json_schema_to_genai_schema(params),
            )
        )
    return genai.types.Tool(function_declarations=declarations)


class GeminiClient(LLMClient):
    """Google Gemini LLMクライアント"""

    def __init__(self):
        from config import GEMINI_API_KEY, GEMINI_API_KEY_BACKUP, GEMINI_MODEL
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        self._backup_client = (
            genai.Client(api_key=GEMINI_API_KEY_BACKUP)
            if GEMINI_API_KEY_BACKUP else None
        )
        self._using_backup = False
        self._default_model = GEMINI_MODEL

    async def generate(
        self,
        config: LLMConfig,
        messages: list[dict],
    ) -> LLMResponse | None:
        model = config.model or self._default_model

        # contentsの構築
        contents = []
        for msg in messages:
            parts_data = msg.get("parts", [])
            genai_parts = []
            for p in parts_data:
                if isinstance(p, ContentPart):
                    if p.image_bytes:
                        genai_parts.append(
                            genai.types.Part.from_bytes(
                                data=p.image_bytes, mime_type=p.mime_type,
                            )
                        )
                    if p.text:
                        genai_parts.append(genai.types.Part(text=p.text))
                    if p.tool_call:
                        # tool_call in assistant message → function_call part
                        genai_parts.append(
                            genai.types.Part(
                                function_call=genai.types.FunctionCall(
                                    name=p.tool_call.name,
                                    args=p.tool_call.args,
                                )
                            )
                        )
                elif isinstance(p, genai.types.Part):
                    # 直接genai.types.Partが渡された場合（後方互換）
                    genai_parts.append(p)
                elif isinstance(p, dict):
                    # dict形式: {"text": ...} or {"function_response": ...}
                    if "text" in p:
                        genai_parts.append(genai.types.Part(text=p["text"]))
                    elif "function_response" in p:
                        fr = p["function_response"]
                        genai_parts.append(
                            genai.types.Part.from_function_response(
                                name=fr["name"], response=fr["response"],
                            )
                        )

            role = msg.get("role", "user")
            # Geminiは "model" を使う
            if role == "assistant":
                role = "model"
            contents.append(genai.types.Content(role=role, parts=genai_parts))

        # GenerateContentConfig
        gen_config = genai.types.GenerateContentConfig(
            system_instruction=config.system_prompt or None,
            temperature=config.temperature,
            max_output_tokens=config.max_tokens,
        )

        # Thinking config
        if config.thinking_budget is not None:
            gen_config.thinking_config = genai.types.ThinkingConfig(
                thinking_budget=config.thinking_budget,
            )

        # Tools
        if config.tools:
            gen_config.tools = [_tool_definitions_to_gemini(config.tools)]

        # API呼び出し（リトライ + バックアップキー切替）
        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=gen_config,
                    ),
                    timeout=120,
                )
                return self._parse_response(response)

            except asyncio.TimeoutError:
                logger.error(f"Gemini API timeout (attempt {attempt + 1})")
                if attempt == 0:
                    continue
                return None

            except Exception as e:
                error_str = str(e).lower()
                is_quota = any(
                    x in error_str
                    for x in ("429", "quota", "rate", "resource exhausted")
                )

                if is_quota and self._backup_client and not self._using_backup:
                    logger.warning("Gemini main key quota hit, switching to backup")
                    self._using_backup = True
                    try:
                        response = await asyncio.wait_for(
                            self._backup_client.aio.models.generate_content(
                                model=model, contents=contents, config=gen_config,
                            ),
                            timeout=120,
                        )
                        return self._parse_response(response)
                    except Exception as backup_err:
                        logger.error(f"Backup key also failed: {backup_err}")

                logger.error(f"Gemini API error (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    continue
                return None

        return None

    def _parse_response(self, response) -> LLMResponse | None:
        """Geminiレスポンス → LLMResponse に変換"""
        if not response.candidates:
            logger.warning("Empty candidates from Gemini")
            return None

        candidate = response.candidates[0]

        # Safety filter check
        if hasattr(candidate, "finish_reason") and str(candidate.finish_reason) == "SAFETY":
            logger.warning("Response blocked by safety filter")
            return LLMResponse(finish_reason="safety", raw=response)

        if not candidate.content or not candidate.content.parts:
            logger.warning("Empty parts from Gemini")
            return None

        parts = []
        has_tool_calls = False
        for p in candidate.content.parts:
            if p is None:
                continue
            if p.function_call:
                has_tool_calls = True
                parts.append(ContentPart(
                    tool_call=UnifiedToolCall(
                        name=p.function_call.name,
                        args=dict(p.function_call.args) if p.function_call.args else {},
                    )
                ))
            elif p.text:
                parts.append(ContentPart(text=p.text))

        finish = "tool_use" if has_tool_calls else "stop"
        return LLMResponse(parts=parts, finish_reason=finish, raw=response)

    def format_tool_result(self, tool_call_id: str, tool_name: str, result: dict) -> dict:
        """ツール結果をGemini形式のメッセージに変換"""
        return {
            "role": "user",
            "parts": [ContentPart(
                text=None,
            )],
            "_raw_parts": [
                genai.types.Part.from_function_response(
                    name=tool_name, response=result,
                )
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
