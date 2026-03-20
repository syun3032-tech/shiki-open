"""マルチエージェント・オーケストレーション

識がコーディネーターとして、専門サブエージェントを並列実行する。
サブエージェントはGemini Flashで動作（コスト効率重視）。

サブエージェント:
  - researcher: Web検索・情報収集（search_web, browse_url等のツール使用）
  - coder: Claude Code委譲によるコーディング
  - writer: テキスト生成・要約（ツールなし）
  - analyst: データ分析・比較（execute_code使用）
"""

import asyncio
import json
import logging
import re

import google.genai as genai

from config import GEMINI_API_KEY

logger = logging.getLogger("shiki.agent")

# サブエージェント用モデル（コスト効率重視）
_SUB_AGENT_MODEL = "gemini-2.5-flash"

# サブエージェントタイムアウト（秒）
_SUB_AGENT_TIMEOUT = 120

# Geminiクライアント
_client = genai.Client(api_key=GEMINI_API_KEY)

# === サブエージェント定義 ===

_AGENT_PROMPTS = {
    "researcher": (
        "あなたはWeb調査の専門家。与えられたタスクに関する情報をWebから収集する。\n"
        "使えるツール: search_web（Google検索）, browse_url（ページ読み取り）, "
        "get_page_text（テキスト取得）, get_accessibility_tree（ページ構造取得）。\n"
        "手順: まずsearch_webで検索し、有望な結果をbrowse_urlで詳細読み取り。\n"
        "結果は日本語で簡潔にまとめること。出典URLも含める。"
    ),
    "coder": (
        "あなたはコーディングの専門家。Claude Code（Anthropic社のAIコーディングエージェント）に"
        "タスクを委譲してコードを書かせる。\n"
        "delegate_to_claudeツールを使って具体的な指示を出すこと。\n"
        "taskには何を実装するか、contextには背景情報を具体的に書く。"
    ),
    "writer": (
        "あなたは文章作成の専門家。与えられたタスクに基づいて、"
        "高品質な文章を作成する。日本語で、読みやすく、構造化された文章を書くこと。\n"
        "箇条書き・見出し・段落を適切に使い分ける。"
    ),
    "analyst": (
        "あなたはデータ分析の専門家。Pythonコードを実行してデータを分析する。\n"
        "execute_codeツールでPythonコードを実行できる。\n"
        "使えるモジュール: math, json, re, datetime, collections, statistics, csv等。\n"
        "結果はprint()で出力し、分析結果を日本語で簡潔にまとめること。"
    ),
}

# researcherが使えるツール（function calling用）
_RESEARCHER_TOOLS = genai.types.Tool(function_declarations=[
    genai.types.FunctionDeclaration(
        name="search_web",
        description="Google検索してトップ10結果を返す。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "query": genai.types.Schema(type="STRING", description="検索クエリ"),
            },
            required=["query"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="browse_url",
        description="URLのページ内容をテキストで取得する。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="取得するURL"),
            },
            required=["url"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_page_text",
        description="URLからテキストのみ取得（軽量版）。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="取得するURL"),
            },
            required=["url"],
        ),
    ),
    genai.types.FunctionDeclaration(
        name="get_accessibility_tree",
        description="URLのAccessibility Tree（UI構造）をテキストで取得。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "url": genai.types.Schema(type="STRING", description="対象ページURL"),
            },
            required=["url"],
        ),
    ),
])

# coderが使えるツール
_CODER_TOOLS = genai.types.Tool(function_declarations=[
    genai.types.FunctionDeclaration(
        name="delegate_to_claude",
        description="Claude Codeにコーディングタスクを委譲する。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "task": genai.types.Schema(type="STRING", description="具体的なコーディング指示"),
                "context": genai.types.Schema(type="STRING", description="追加コンテキスト"),
                "cwd": genai.types.Schema(type="STRING", description="作業ディレクトリ"),
                "timeout": genai.types.Schema(type="INTEGER", description="タイムアウト秒数（デフォルト300）"),
            },
            required=["task"],
        ),
    ),
])

# analystが使えるツール
_ANALYST_TOOLS = genai.types.Tool(function_declarations=[
    genai.types.FunctionDeclaration(
        name="execute_code",
        description="Pythonコードを実行する。結果はprint()で出力。",
        parameters=genai.types.Schema(
            type="OBJECT",
            properties={
                "code": genai.types.Schema(type="STRING", description="実行するPythonコード"),
            },
            required=["code"],
        ),
    ),
])

_AGENT_TOOLS = {
    "researcher": _RESEARCHER_TOOLS,
    "coder": _CODER_TOOLS,
    "analyst": _ANALYST_TOOLS,
    # writer はツールなし
}

# 自動検出用キーワード
_AGENT_KEYWORDS = {
    "researcher": [
        "調べて", "検索", "探して", "リサーチ", "情報", "ニュース", "最新",
        "search", "research", "find", "look up", "web", "ネット", "サイト",
        "URL", "ページ", "記事",
    ],
    "coder": [
        "コード", "実装", "プログラム", "開発", "デバッグ", "修正", "リファクタ",
        "code", "implement", "develop", "debug", "fix", "refactor",
        "関数", "クラス", "モジュール", "API", "エンドポイント", "書いて",
    ],
    "writer": [
        "書いて", "文章", "要約", "まとめ", "レポート", "メール", "文面",
        "write", "draft", "summarize", "summary", "report", "article",
        "ブログ", "説明", "翻訳", "translate",
    ],
    "analyst": [
        "分析", "比較", "計算", "統計", "データ", "グラフ",
        "analyze", "compare", "calculate", "statistics", "data",
        "集計", "平均", "合計", "割合",
    ],
}


def _auto_detect_agents(task: str) -> list[str]:
    """タスクテキストからどのサブエージェントを使うか自動判定"""
    task_lower = task.lower()
    scores: dict[str, int] = {}

    for agent_type, keywords in _AGENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in task_lower)
        if score > 0:
            scores[agent_type] = score

    if not scores:
        # デフォルト: writerを使う（汎用的に回答）
        return ["writer"]

    # スコア降順で返す
    sorted_agents = sorted(scores.keys(), key=lambda a: scores[a], reverse=True)
    return sorted_agents


async def _run_tool(tool_name: str, tool_args: dict) -> dict:
    """サブエージェント用ツール実行（SecurityGate経由）"""
    from agent.tools_config import TOOL_FUNCTIONS, validate_tool_args
    from security.gate import SecurityGate, TOOL_LEVELS, ToolLevel
    from security.anomaly_detector import anomaly_detector
    from config import LOG_DIR

    tool_fn = TOOL_FUNCTIONS.get(tool_name)
    if not tool_fn:
        return {"error": f"未知のツール: {tool_name}"}

    # 引数バリデーション
    validation_error = validate_tool_args(tool_name, tool_args)
    if validation_error:
        return {"error": validation_error}

    # SecurityGateチェック
    gate = SecurityGate(LOG_DIR)
    approved, reason = await gate.check_permission(tool_name, tool_args)
    if not approved:
        logger.warning(f"Sub-agent tool BLOCKED: {tool_name} - {reason}")
        return {"error": f"ブロック: {reason}", "blocked": True}

    try:
        result = await tool_fn(**tool_args)
        return result
    except Exception as e:
        logger.error(f"Sub-agent tool error: {tool_name} - {e}", exc_info=True)
        anomaly_detector.record_event("failed_tool_calls", f"sub_agent:{tool_name}:{e}")
        return {"error": f"{type(e).__name__}: {str(e)}"}


async def _run_sub_agent_with_tools(
    agent_type: str, task: str, context: str, tools: genai.types.Tool
) -> str:
    """ツール付きサブエージェントを実行（1ラウンドのfunction calling）"""
    system_prompt = _AGENT_PROMPTS[agent_type]
    user_text = f"タスク: {task}"
    if context:
        user_text += f"\n\nコンテキスト: {context}"

    contents = [
        genai.types.Content(
            role="user",
            parts=[genai.types.Part(text=user_text)],
        )
    ]

    config = genai.types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.3,
        max_output_tokens=2048,
        tools=[tools],
        thinking_config=genai.types.ThinkingConfig(thinking_budget=1024),
    )

    # 最大3ラウンドのツール呼び出しを許可
    collected_results = []
    for round_num in range(3):
        response = await asyncio.wait_for(
            _client.aio.models.generate_content(
                model=_SUB_AGENT_MODEL,
                contents=contents,
                config=config,
            ),
            timeout=_SUB_AGENT_TIMEOUT,
        )

        if not response.candidates or not response.candidates[0].content:
            break

        parts = response.candidates[0].content.parts or []
        function_calls = [p for p in parts if p and p.function_call]
        text_parts = [p.text for p in parts if p and p.text]

        # テキスト応答のみ → 完了
        if not function_calls:
            final_text = " ".join(text_parts) if text_parts else ""
            if collected_results:
                return final_text + "\n\n[ツール実行結果]\n" + "\n".join(collected_results)
            return final_text

        # ツール呼び出しを実行
        contents.append(response.candidates[0].content)
        fn_response_parts = []

        for fc_part in function_calls:
            fc = fc_part.function_call
            tool_name = fc.name
            tool_args = dict(fc.args) if fc.args else {}

            logger.info(f"Sub-agent [{agent_type}] calling: {tool_name}({tool_args})")
            result = await _run_tool(tool_name, tool_args)
            collected_results.append(f"{tool_name}: {str(result)[:500]}")

            fn_response_parts.append(
                genai.types.Part.from_function_response(name=tool_name, response=result)
            )

        contents.append(
            genai.types.Content(role="user", parts=fn_response_parts)
        )

    # ラウンド上限到達 → 収集した結果を返す
    return "\n".join(collected_results) if collected_results else "ツール実行の結果を取得できませんでした。"


async def _run_sub_agent_text_only(
    agent_type: str, task: str, context: str
) -> str:
    """テキストのみサブエージェント（writerなど）"""
    system_prompt = _AGENT_PROMPTS[agent_type]
    user_text = f"タスク: {task}"
    if context:
        user_text += f"\n\nコンテキスト: {context}"

    config = genai.types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.7,
        max_output_tokens=2048,
        thinking_config=genai.types.ThinkingConfig(thinking_budget=512),
    )

    response = await asyncio.wait_for(
        _client.aio.models.generate_content(
            model=_SUB_AGENT_MODEL,
            contents=[
                genai.types.Content(
                    role="user",
                    parts=[genai.types.Part(text=user_text)],
                )
            ],
            config=config,
        ),
        timeout=_SUB_AGENT_TIMEOUT,
    )

    if not response.candidates:
        return "応答を生成できませんでした。"

    return response.text or ""


async def _run_sub_agent(agent_type: str, task: str, context: str) -> tuple[str, str]:
    """サブエージェントを実行し、(agent_type, result)を返す"""
    try:
        tools = _AGENT_TOOLS.get(agent_type)
        if tools:
            result = await _run_sub_agent_with_tools(agent_type, task, context, tools)
        else:
            result = await _run_sub_agent_text_only(agent_type, task, context)
        logger.info(f"Sub-agent [{agent_type}] completed: {result[:100]}...")
        return agent_type, result
    except asyncio.TimeoutError:
        logger.warning(f"Sub-agent [{agent_type}] timed out")
        return agent_type, f"[タイムアウト] {agent_type}の処理が{_SUB_AGENT_TIMEOUT}秒を超えました。"
    except Exception as e:
        logger.error(f"Sub-agent [{agent_type}] error: {e}", exc_info=True)
        return agent_type, f"[エラー] {agent_type}: {type(e).__name__}: {str(e)}"


async def _generate_summary(task: str, results: dict[str, str]) -> str:
    """コーディネーターが各サブエージェントの結果を統合要約する"""
    results_text = "\n\n".join(
        f"## {agent_type}の結果\n{result}"
        for agent_type, result in results.items()
    )

    system_prompt = (
        "あなたはAIコーディネーター。複数の専門エージェントの結果を統合し、"
        "ユーザーのタスクに対する最終回答をまとめる。"
        "各エージェントの結果を整理し、矛盾があれば指摘し、"
        "簡潔で分かりやすい日本語でまとめること。"
    )

    user_text = f"# 元のタスク\n{task}\n\n# 各エージェントの結果\n{results_text}"

    config = genai.types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.3,
        max_output_tokens=2048,
        thinking_config=genai.types.ThinkingConfig(thinking_budget=1024),
    )

    try:
        response = await asyncio.wait_for(
            _client.aio.models.generate_content(
                model=_SUB_AGENT_MODEL,
                contents=[
                    genai.types.Content(
                        role="user",
                        parts=[genai.types.Part(text=user_text)],
                    )
                ],
                config=config,
            ),
            timeout=60,
        )
        if response.candidates:
            return response.text or ""
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")

    # フォールバック: 結果をそのまま連結
    return results_text


async def dispatch_agents(
    task: str,
    agents: list[str] | None = None,
    context: str = "",
) -> dict:
    """タスクを複数の専門サブエージェントに並列ディスパッチする。

    Args:
        task: タスクの説明
        agents: 使用するサブエージェントのリスト。Noneなら自動検出。
            有効値: "researcher", "coder", "writer", "analyst"
        context: 追加コンテキスト

    Returns:
        {
            "success": True,
            "results": {"researcher": "...", "coder": "...", ...},
            "summary": "統合された最終回答",
            "agents_used": ["researcher", "coder"],
        }
    """
    # エージェント選択
    valid_agents = set(_AGENT_PROMPTS.keys())

    if agents:
        # カンマ区切り文字列の場合も対応
        if isinstance(agents, str):
            agents = [a.strip() for a in agents.split(",")]
        selected = [a for a in agents if a in valid_agents]
        if not selected:
            return {
                "success": False,
                "error": f"有効なエージェントがありません。選択肢: {', '.join(valid_agents)}",
            }
    else:
        selected = _auto_detect_agents(task)

    logger.info(f"Dispatching to sub-agents: {selected} for task: {task[:80]}")

    # 全サブエージェントを並列実行
    tasks = [_run_sub_agent(agent_type, task, context) for agent_type in selected]
    completed = await asyncio.gather(*tasks)

    results = {agent_type: result for agent_type, result in completed}

    # 結果統合（サブエージェントが1つだけなら要約不要）
    if len(results) == 1:
        summary = next(iter(results.values()))
    else:
        summary = await _generate_summary(task, results)

    return {
        "success": True,
        "results": results,
        "summary": summary,
        "agents_used": selected,
    }
