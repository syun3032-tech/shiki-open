"""MCP Client - 外部サービス連携基盤

MCPサーバーに接続し、ツールを動的に取得・実行する。
設定ファイルベースでサーバーを追加可能。

Architecture:
  識ちゃん(FastAPI) → MCP Client → MCP Servers
                                    ├── GitHub
                                    ├── Fetch (Web取得)
                                    └── ... (追加可能)
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("shiki.mcp")

# MCP設定ファイル
_MCP_CONFIG_FILE = Path(__file__).parent / "mcp_servers.json"

# アクティブなセッション管理
_sessions: dict[str, ClientSession] = {}
_exit_stack: AsyncExitStack | None = None
_available_tools: dict[str, dict] = {}  # tool_name -> {server, schema}


def load_mcp_config() -> dict:
    """MCP設定ファイルをロード（パーミッションチェック付き）"""
    if not _MCP_CONFIG_FILE.exists():
        return {"servers": {}}
    try:
        # ファイル権限チェック（他ユーザーに書き込み権限がないことを確認）
        import sys
        if sys.platform != "win32":
            mode = oct(_MCP_CONFIG_FILE.stat().st_mode)[-3:]
            if mode not in ("600", "644", "400", "440"):
                logger.warning(f"MCP config permissions too open: {mode}. Fixing to 600.")
                _MCP_CONFIG_FILE.chmod(0o600)
        return json.loads(_MCP_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"MCP config load failed: {e}")
        return {"servers": {}}


def _resolve_env_vars(env: dict | None) -> dict | None:
    """${VAR}形式の環境変数参照を実際の値に解決"""
    if not env:
        return env
    resolved = {}
    for key, value in env.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_name = value[2:-1]
            resolved[key] = os.environ.get(env_name, "")
        else:
            resolved[key] = value
    return resolved


_MCP_INIT_TIMEOUT = 30  # MCP初期化タイムアウト（秒）


async def connect_server(name: str, config: dict) -> bool:
    """MCPサーバーに接続

    Args:
        name: サーバー名（例: "github"）
        config: {command, args, env}
    """
    global _exit_stack
    if _exit_stack is None:
        _exit_stack = AsyncExitStack()

    try:
        server_params = StdioServerParameters(
            command=config["command"],
            args=config.get("args", []),
            env=_resolve_env_vars(config.get("env")),
        )

        stdio_transport = await _exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read_stream, write_stream = stdio_transport
        session = await _exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await asyncio.wait_for(session.initialize(), timeout=_MCP_INIT_TIMEOUT)

        _sessions[name] = session

        # ツール一覧を取得・登録
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            tool_key = f"mcp_{name}_{tool.name}"
            _available_tools[tool_key] = {
                "server": name,
                "mcp_tool_name": tool.name,
                "description": tool.description or "",
                "schema": tool.inputSchema if hasattr(tool, 'inputSchema') else {},
            }
            logger.info(f"MCP tool registered: {tool_key}")

        logger.info(f"MCP server '{name}' connected ({len(tools_result.tools)} tools)")
        return True

    except FileNotFoundError:
        logger.warning(f"MCP server '{name}': command not found ({config.get('command')})")
        return False
    except Exception as e:
        logger.error(f"MCP server '{name}' connection failed: {e}")
        return False


async def call_tool(tool_key: str, arguments: dict) -> Any:
    """MCPツールを実行

    Args:
        tool_key: "mcp_{server}_{tool_name}" 形式
        arguments: ツール引数
    """
    tool_info = _available_tools.get(tool_key)
    if not tool_info:
        return {"error": f"MCP tool not found: {tool_key}"}

    server_name = tool_info["server"]
    session = _sessions.get(server_name)
    if not session:
        return {"error": f"MCP server not connected: {server_name}"}

    try:
        result = await asyncio.wait_for(
            session.call_tool(
                tool_info["mcp_tool_name"],
                arguments=arguments,
            ),
            timeout=60,  # MCPツール実行タイムアウト
        )
        # MCP結果をdict形式に変換
        if result.content:
            texts = []
            for content in result.content:
                if hasattr(content, 'text'):
                    texts.append(content.text)
            return {"success": True, "output": "\n".join(texts)}
        return {"success": True, "output": ""}

    except asyncio.TimeoutError:
        logger.error(f"MCP tool call timed out: {tool_key}")
        return {"error": f"MCPツール '{tool_key}' がタイムアウト（60秒）"}
    except Exception as e:
        logger.error(f"MCP tool call failed: {tool_key} - {e}")
        return {"error": str(e)}


def get_available_tools() -> dict[str, dict]:
    """利用可能なMCPツール一覧"""
    return _available_tools.copy()


async def connect_all_servers() -> int:
    """設定ファイルの全サーバーに接続"""
    config = load_mcp_config()
    servers = config.get("servers", {})

    if not servers:
        logger.info("No MCP servers configured")
        return 0

    connected = 0
    for name, server_config in servers.items():
        if not server_config.get("enabled", True):
            logger.info(f"MCP server '{name}' is disabled, skipping")
            continue
        if await connect_server(name, server_config):
            connected += 1

    logger.info(f"MCP: {connected}/{len(servers)} servers connected")
    return connected


async def disconnect_all():
    """全MCPサーバーを切断"""
    global _exit_stack
    if _exit_stack:
        await _exit_stack.aclose()
        _exit_stack = None
    _sessions.clear()
    _available_tools.clear()
    logger.info("All MCP servers disconnected")
