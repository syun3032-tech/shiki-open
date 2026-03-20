"""MCP-Gemini Bridge

MCPツールをGemini Function Calling用のツール定義に自動変換し、
agent/tools_config.pyのTOOL_FUNCTIONSに動的登録する。

使い方:
  await register_mcp_tools()  # 起動時に1回呼ぶ
"""

import logging

import google.genai as genai

from mcp_ext.client import get_available_tools, call_tool
from security.gate import ToolLevel

logger = logging.getLogger("shiki.mcp")

# JSON Schema type → Gemini Schema type
_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _json_schema_to_gemini(schema: dict) -> genai.types.Schema:
    """JSON SchemaをGemini Schemaに変換（配列・ネスト対応）"""
    schema_type = _TYPE_MAP.get(schema.get("type", "string"), "STRING")
    kwargs: dict = {"type": schema_type}

    desc = schema.get("description", "")
    if desc:
        kwargs["description"] = desc

    # 配列型: items必須
    if schema_type == "ARRAY":
        items_schema = schema.get("items", {"type": "string"})
        kwargs["items"] = _json_schema_to_gemini(items_schema)

    # オブジェクト型: properties
    if "properties" in schema:
        properties = {}
        for prop_name, prop_schema in schema["properties"].items():
            properties[prop_name] = _json_schema_to_gemini(prop_schema)
        kwargs["properties"] = properties
        required = schema.get("required", [])
        if required:
            kwargs["required"] = required

    return genai.types.Schema(**kwargs)


def build_mcp_gemini_declarations() -> list:
    """MCPツールをGemini FunctionDeclarationのリストに変換"""
    mcp_tools = get_available_tools()
    declarations = []

    for tool_key, tool_info in mcp_tools.items():
        schema = tool_info.get("schema", {})
        gemini_schema = _json_schema_to_gemini(schema) if schema else genai.types.Schema(
            type="OBJECT", properties={}
        )

        declarations.append(
            genai.types.FunctionDeclaration(
                name=tool_key,
                description=f"[MCP] {tool_info['description'][:200]}",
                parameters=gemini_schema,
            )
        )

    return declarations


def build_mcp_tool_functions() -> dict:
    """MCPツールをTOOL_FUNCTIONS形式に変換（関数のみ、レベルはTOOL_LEVELSに登録）"""
    mcp_tools = get_available_tools()
    functions = {}

    for tool_key in mcp_tools:
        functions[tool_key] = _make_mcp_caller(tool_key)

    return functions


def _make_mcp_caller(tool_key: str):
    """MCPツール呼び出しラムダを生成"""
    async def caller(**kwargs):
        return await call_tool(tool_key, kwargs)
    return caller


# READ系MCPツールのキーワード（通知不要な読み取り専用操作）
_MCP_READ_KEYWORDS = frozenset({
    "get", "list", "search", "query", "read", "fetch",
    "describe", "retrieve", "find", "count", "check",
    "current-time", "get-block-children", "get-page",
})


# 明示的にブロックするMCPツール（DESTRUCTIVE = 実行拒否）
_MCP_BLOCKED_TOOLS = frozenset({
    "mcp_gmail_send_email",        # メール送信は禁止（下書きまで）
    "mcp_gmail_batch_delete_emails",  # 一括削除は危険
})


def _classify_mcp_tool_level(tool_key: str, description: str) -> ToolLevel:
    """MCPツールのセキュリティレベルを自動判定"""
    # ブロックリストに該当 → DESTRUCTIVE（実行拒否）
    if tool_key in _MCP_BLOCKED_TOOLS:
        return ToolLevel.DESTRUCTIVE

    name_lower = tool_key.lower()
    desc_lower = description.lower()

    # ツール名 or 説明文にREADキーワードが含まれる → READ
    for keyword in _MCP_READ_KEYWORDS:
        if keyword in name_lower or keyword in desc_lower:
            # ただし更新・作成系キーワードもある場合はELEVATED
            write_keywords = {"create", "update", "delete", "post", "patch", "put", "remove", "write"}
            if any(wk in name_lower for wk in write_keywords):
                return ToolLevel.ELEVATED
            return ToolLevel.READ

    return ToolLevel.ELEVATED


async def register_mcp_tools():
    """MCPツールをagent/tools_config.pyに動的登録

    起動時にconnect_all_servers()の後に呼ぶ。
    二重登録を防止する。
    """
    from agent.tools_config import TOOL_FUNCTIONS, TOOL_STATUS_MESSAGES, GEMINI_TOOLS

    mcp_functions = build_mcp_tool_functions()
    mcp_tools = get_available_tools()

    # 既存のGemini宣言名を取得（二重登録防止）
    existing_gemini_names = {fd.name for fd in GEMINI_TOOLS.function_declarations}

    registered = 0
    for tool_key, tool_fn in mcp_functions.items():
        if tool_key in TOOL_FUNCTIONS:
            logger.debug(f"MCP tool already registered, skipping: {tool_key}")
            continue
        TOOL_FUNCTIONS[tool_key] = tool_fn
        desc = mcp_tools[tool_key]["description"]
        TOOL_STATUS_MESSAGES[tool_key] = f"[MCP] {desc[:30]}..."
        registered += 1

    # GEMINI_TOOLSにもFunctionDeclarationを追加（Geminiがツールを認識するために必須）
    mcp_declarations = build_mcp_gemini_declarations()
    added_decls = 0
    for decl in mcp_declarations:
        if decl.name not in existing_gemini_names:
            GEMINI_TOOLS.function_declarations.append(decl)
            added_decls += 1

    if registered:
        logger.info(f"Registered {registered} MCP tools ({added_decls} Gemini declarations)")

    # SecurityGateにもレベル別で登録
    from security.gate import TOOL_LEVELS
    read_count = 0
    for tool_key in mcp_functions:
        if tool_key not in TOOL_LEVELS:
            desc = mcp_tools[tool_key]["description"]
            level = _classify_mcp_tool_level(tool_key, desc)
            TOOL_LEVELS[tool_key] = level
            if level == ToolLevel.READ:
                read_count += 1

    if read_count:
        logger.info(f"MCP tools: {read_count} READ, {len(mcp_functions) - read_count} ELEVATED")
