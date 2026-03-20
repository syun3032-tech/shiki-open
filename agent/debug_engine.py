"""Systematic Debugging Engine — OpenClaw inspired

ツール失敗が蓄積した時に、段階的にデバッグ手法を
システムプロンプトに注入する。

Phase:
  normal → diagnosing → pattern_analysis → hypothesis → architecture_review

3種類以上のツールが失敗 or 5回以上失敗 → アプローチ自体を疑う。
"""

import logging
from collections import defaultdict

logger = logging.getLogger("shiki.agent")

# === 状態 ===
_task_failures: list[dict] = []
_unique_tool_failures: dict[str, int] = defaultdict(int)
_total_failures: int = 0
_phase: str = "normal"


def reset_debug_state():
    """タスク開始時にリセット"""
    global _task_failures, _unique_tool_failures, _total_failures, _phase
    _task_failures = []
    _unique_tool_failures = defaultdict(int)
    _total_failures = 0
    _phase = "normal"


def record_debug_failure(tool_name: str, error: str, failure_type: str):
    """ツール失敗を記録し、フェーズ遷移を判定"""
    global _total_failures, _phase

    _task_failures.append({
        "tool": tool_name,
        "error": error[:200],
        "type": failure_type,
    })
    _unique_tool_failures[tool_name] += 1
    _total_failures += 1

    # フェーズ遷移
    unique_count = len(_unique_tool_failures)

    if unique_count >= 3 or _total_failures >= 5:
        _phase = "architecture_review"
    elif _total_failures >= 3 or any(c >= 3 for c in _unique_tool_failures.values()):
        _phase = "hypothesis"
    elif _total_failures >= 2:
        _phase = "pattern_analysis"
    elif any(c >= 2 for c in _unique_tool_failures.values()):
        _phase = "diagnosing"

    if _phase != "normal":
        logger.info(f"Debug phase: {_phase} (total={_total_failures}, unique={unique_count})")


def get_debug_injection() -> str:
    """現在のフェーズに応じたデバッグプロンプトを返す"""
    if _phase == "normal":
        return ""

    # 失敗サマリー
    summary = "\n".join(
        f"- {t['tool']}: {t['error'][:80]} ({t['type']})"
        for t in _task_failures[-5:]
    )

    if _phase == "diagnosing":
        last = _task_failures[-1]
        return (
            f"\n\n# デバッグモード: 原因分析\n"
            f"{last['tool']}が複数回失敗している。同じ方法を繰り返す前に:\n"
            f"1. エラーメッセージを正確に読め: {last['error'][:100]}\n"
            f"2. 前提条件（ページ読み込み済み？要素存在？画面状態？）を確認しろ\n"
            f"3. 別のLayer/ツールで同じ目的を達成できないか考えろ\n"
            f"4. take_screenshotで現在の状態を確認してから次のアクションを決めろ"
        )

    if _phase == "pattern_analysis":
        return (
            f"\n\n# デバッグモード: パターン分析\n"
            f"複数の失敗が発生している:\n{summary}\n\n"
            f"共通パターンはあるか？\n"
            f"- 全部タイムアウト → ネットワーク/負荷の問題\n"
            f"- 全部同じサイト → サイト側の問題\n"
            f"- 全部座標系 → スクショと実画面のずれ\n"
            f"- 全部権限エラー → セキュリティ設定の問題\n"
            f"パターンを特定してから次のアクションを決めろ"
        )

    if _phase == "hypothesis":
        return (
            f"\n\n# デバッグモード: 仮説検証\n"
            f"失敗が続いている:\n{summary}\n\n"
            f"1. take_screenshotで現在の画面状態を確認しろ\n"
            f"2. 想定と実際の状態の差を特定しろ\n"
            f"3. 1つだけ変数を変えてリトライしろ\n"
            f"4. それでもダメなら根本的にアプローチを変えろ"
        )

    if _phase == "architecture_review":
        return (
            f"\n\n# デバッグモード: アプローチ再考（重要）\n"
            f"{len(_unique_tool_failures)}種類のツールが失敗している。"
            f"今のアプローチ自体が間違っている可能性が高い。\n{summary}\n\n"
            f"- 本当にこのLayerが正しいか？ Layer 2↔4 の切り替えを検討\n"
            f"- そもそもタスクの理解が正しいか？\n"
            f"- オーナーに確認を取った方が良いかもしれない\n"
            f"- 「うまくいかなかった」と正直に報告するのも正解\n"
            f"- 同じツールを同じ引数で繰り返すな"
        )

    return ""


def get_debug_stats() -> dict:
    """デバッグ状態を返す（ログ/ヘルスチェック用）"""
    return {
        "phase": _phase,
        "total_failures": _total_failures,
        "unique_tools_failed": len(_unique_tool_failures),
        "details": dict(_unique_tool_failures),
    }
