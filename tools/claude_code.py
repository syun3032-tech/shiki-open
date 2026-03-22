"""Claude Code委譲ツール

識（Gemini）がClaude Code CLIに作業を委譲するためのラッパー。
コーディング、デバッグ、リファクタリング、調査、設計、壁打ちなど
知的作業をClaude Codeに任せる。

使い方:
  result = await delegate_to_claude(
      task="このPythonスクリプトのバグを修正して",
      context="エラー: TypeError at line 42",
      cwd="~/Desktop/myproject",
  )
"""

import asyncio
import json
import logging
import shutil

logger = logging.getLogger("shiki.tools")

# Claude Code CLI のパス（自動検出）
_CLAUDE_PATH: str | None = None

# デフォルト設定
DEFAULT_TIMEOUT = 300  # 5分
MAX_TIMEOUT = 900  # 15分
DEFAULT_MAX_TURNS = 15
MAX_OUTPUT_SIZE = 50_000  # 50KB


def _find_claude() -> str:
    """Claude Code CLIのパスを検出"""
    global _CLAUDE_PATH
    if _CLAUDE_PATH:
        return _CLAUDE_PATH

    path = shutil.which("claude")
    if path:
        _CLAUDE_PATH = path
        return path

    # よくあるインストール先を探す
    import sys
    common_paths = [
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    if sys.platform == "win32":
        import os
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        common_paths = [
            os.path.join(appdata, "npm", "claude.cmd"),
            os.path.join(localappdata, "npm", "claude.cmd"),
            os.path.join(appdata, "npm", "claude"),
            os.path.join(localappdata, "npm", "claude"),
        ]
    for p in common_paths:
        import os
        if os.path.isfile(p) and os.access(p, os.X_OK):
            _CLAUDE_PATH = p
            return p

    raise FileNotFoundError(
        "Claude Code CLIが見つからない。`npm install -g @anthropic-ai/claude-code` でインストールしてください"
    )


MAX_PROMPT_SIZE = 6000  # Claude Code stdin ~7000文字でempty output bugあり（issue #7263）


def _build_prompt(task: str, context: str | None = None) -> str:
    """Claude Codeに渡すプロンプトを構築（サイズ制限 + 機密情報スキャン付き）"""
    from security.output_validator import scan_output_for_leaks

    # task/contextに機密情報が混入していないかチェック
    for label, text in [("task", task), ("context", context or "")]:
        leaks = scan_output_for_leaks(text)
        if leaks:
            logger.warning(f"Credential leak detected in {label}: {leaks}")
            raise ValueError(f"委譲プロンプトの{label}に機密情報が含まれている: {leaks}")

    parts = [task]
    if context:
        # コンテキストが長すぎる場合は切り詰める
        remaining = MAX_PROMPT_SIZE - len(task) - 20
        if remaining > 100:
            context = context[:remaining]
        else:
            context = context[:500]
        parts.append(f"\n\n## コンテキスト\n{context}")
    return "\n".join(parts)


async def delegate_to_claude(
    task: str,
    context: str | None = None,
    cwd: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_turns: int = DEFAULT_MAX_TURNS,
    allowed_tools: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Claude Codeにタスクを委譲して結果を返す

    Args:
        task: タスクの説明
        context: 追加コンテキスト（エラーメッセージ、要件等）
        cwd: 作業ディレクトリ（省略時はプロジェクトルート）
        timeout: タイムアウト秒数（デフォルト300秒=5分、最大900秒=15分）
        max_turns: 最大ターン数（デフォルト15）
        allowed_tools: 許可ツール（カンマ区切り。例: "Read,Edit,Bash"）
        session_id: 前回セッションを継続する場合のID

    Returns:
        dict: {success, result, session_id, cost_usd, tokens_used, error}
    """
    try:
        claude_path = _find_claude()
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    # タイムアウト制限
    timeout = min(max(timeout, 30), MAX_TIMEOUT)

    # プロンプト構築
    prompt = _build_prompt(task, context)

    # コマンド組み立て
    cmd = [
        claude_path,
        "-p", prompt,
        "--output-format", "json",
        "--max-turns", str(max_turns),
    ]

    if allowed_tools:
        for tool in allowed_tools.split(","):
            tool = tool.strip()
            if tool:
                cmd.extend(["--allowedTools", tool])

    if session_id:
        cmd.extend(["--resume", session_id])

    # 作業ディレクトリ
    if not cwd:
        import config
        cwd = str(config.PROJECT_ROOT)

    logger.info(
        f"Claude Code delegation: task='{task[:80]}...', "
        f"cwd={cwd}, timeout={timeout}s, max_turns={max_turns}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        stdout_text = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_SIZE]
        stderr_text = stderr.decode("utf-8", errors="replace")[:5000]

        if proc.returncode != 0:
            logger.warning(
                f"Claude Code failed (exit {proc.returncode}): {stderr_text[:200]}"
            )
            return {
                "success": False,
                "error": f"Claude Code終了コード {proc.returncode}: {stderr_text[:500]}",
                "raw_output": stdout_text[:2000],
            }

        # JSON出力をパース
        try:
            data = json.loads(stdout_text)
            result = {
                "success": True,
                "result": data.get("result", stdout_text[:5000]),
                "session_id": data.get("session_id", ""),
                "cost_usd": data.get("cost_usd"),
            }

            # トークン使用量
            usage = data.get("usage", {})
            if usage:
                result["tokens_used"] = {
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                }

            logger.info(
                f"Claude Code completed: "
                f"cost=${result.get('cost_usd', '?')}, "
                f"session={result.get('session_id', 'none')[:8]}"
            )
            return result

        except json.JSONDecodeError:
            # JSONパース失敗 → テキスト出力として返す
            logger.warning("Claude Code output is not JSON, returning as text")
            return {
                "success": True,
                "result": stdout_text[:5000],
                "session_id": "",
            }

    except asyncio.TimeoutError:
        logger.error(f"Claude Code timed out after {timeout}s")
        # プロセスを強制終了
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return {
            "success": False,
            "error": f"タイムアウト（{timeout}秒）。タスクが大きすぎるかも。分割して再試行してください",
        }
    except Exception as e:
        logger.error(f"Claude Code delegation error: {e}")
        return {"success": False, "error": str(e)}
