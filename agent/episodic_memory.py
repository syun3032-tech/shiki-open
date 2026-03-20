"""エピソード記憶（Manus / Stanford CS329A inspired）

「前回Xしたとき、Yで成功/失敗した」を記録。
次回似たタスクの時に、過去のエピソードを注入して精度UP。

従来のplaybook（手順の再利用）とは異なり、
エピソードは「判断の文脈」を保存する:
- 何を試みたか
- どういう状況だったか
- 結果はどうだったか
- 何を学んだか
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("shiki.episodic")

_EPISODES_FILE = Path(__file__).parent.parent / ".ritsu" / "episodes.json"
MAX_EPISODES = 100

# インメモリキャッシュ
_episodes_cache: list[dict] | None = None


def _load_episodes() -> list[dict]:
    global _episodes_cache
    if _episodes_cache is not None:
        return _episodes_cache
    if _EPISODES_FILE.exists():
        try:
            _episodes_cache = json.loads(_EPISODES_FILE.read_text(encoding="utf-8"))
            return _episodes_cache
        except Exception:
            pass
    _episodes_cache = []
    return []


def _save_episodes(episodes: list[dict]):
    global _episodes_cache
    _episodes_cache = episodes
    try:
        _EPISODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _EPISODES_FILE.write_text(
            json.dumps(episodes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Episode save failed: {e}")


def record_episode(
    task: str,
    tools_used: list[str],
    outcome: str,
    success: bool,
    lesson: str = "",
):
    """タスクエピソードを記録

    Args:
        task: ユーザーの元リクエスト（短縮版）
        tools_used: 使ったツール名のリスト
        outcome: "成功: ..." or "失敗: ..."
        success: 成功したか
        lesson: 学んだこと（自動生成 or 手動）
    """
    episodes = _load_episodes()

    episode = {
        "task": task[:100],
        "tools_used": tools_used[:10],
        "outcome": outcome[:200],
        "success": success,
        "lesson": lesson[:200],
        "timestamp": datetime.now().isoformat(),
        "retrieval_count": 0,
    }

    episodes.append(episode)

    # 上限管理（古いものから削除、ただし成功エピソードを優先保持）
    if len(episodes) > MAX_EPISODES:
        # 失敗エピソードの古いものから削除
        failed = [e for e in episodes if not e.get("success")]
        success_eps = [e for e in episodes if e.get("success")]

        if len(failed) > MAX_EPISODES // 3:
            failed = failed[-(MAX_EPISODES // 3):]

        episodes = failed + success_eps
        if len(episodes) > MAX_EPISODES:
            episodes = episodes[-MAX_EPISODES:]

    _save_episodes(episodes)
    logger.info(f"Episode recorded: {'success' if success else 'failure'} - {task[:50]}")


def find_relevant_episodes(message: str, top_k: int = 3) -> list[dict]:
    """メッセージに関連するエピソードを検索

    キーワードマッチ + 成功優先 + 新しいもの優先
    """
    episodes = _load_episodes()
    if not episodes:
        return []

    msg_lower = message.lower()
    # メッセージからキーワード抽出
    import re
    keywords = set(re.findall(r'[ァ-ヶー]+|[a-zA-Z]{2,}|[一-龥]{2,}', msg_lower))

    scored = []
    for ep in episodes:
        task_lower = ep.get("task", "").lower()

        # キーワードマッチ
        keyword_hits = sum(1 for kw in keywords if kw in task_lower)
        if keyword_hits == 0:
            # ツール名でもマッチ試行
            tools = ep.get("tools_used", [])
            tool_match = any(t.lower() in msg_lower for t in tools)
            if not tool_match:
                continue
            keyword_hits = 0.5

        # スコアリング
        success_bonus = 1.0 if ep.get("success") else 0.3
        lesson_bonus = 0.5 if ep.get("lesson") else 0
        score = keyword_hits * success_bonus + lesson_bonus

        scored.append((score, ep))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for _, ep in scored[:top_k]:
        ep["retrieval_count"] = ep.get("retrieval_count", 0) + 1
        results.append(ep)

    # retrieval_count更新を保存
    if results:
        _save_episodes(_load_episodes())

    return results


def format_episodes_for_prompt(episodes: list[dict]) -> str:
    """エピソードをシステムプロンプト注入用にフォーマット"""
    if not episodes:
        return ""

    lines = ["## 過去の経験（エピソード記憶）"]
    for ep in episodes:
        status = "✓成功" if ep.get("success") else "✗失敗"
        task = ep.get("task", "")
        outcome = ep.get("outcome", "")
        lesson = ep.get("lesson", "")
        tools = ", ".join(ep.get("tools_used", [])[:5])

        lines.append(f"- [{status}] {task}")
        if tools:
            lines.append(f"  使用ツール: {tools}")
        if outcome:
            lines.append(f"  結果: {outcome}")
        if lesson:
            lines.append(f"  教訓: {lesson}")

    return "\n".join(lines)


def get_stats() -> dict:
    """エピソード記憶の統計"""
    episodes = _load_episodes()
    if not episodes:
        return {"total": 0}

    success_count = sum(1 for e in episodes if e.get("success"))
    with_lesson = sum(1 for e in episodes if e.get("lesson"))

    return {
        "total": len(episodes),
        "success": success_count,
        "failure": len(episodes) - success_count,
        "with_lesson": with_lesson,
        "success_rate": round(success_count / len(episodes), 2) if episodes else 0,
    }
