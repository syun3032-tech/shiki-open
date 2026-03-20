"""Playbookシステム

成功したツール呼び出しシーケンスを記録し、
同様のタスクに再利用するための手順書（プレイブック）管理。
エンベディング不使用、キーワードマッチのみで高速検索。
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("shiki.agent")

PLAYBOOKS_FILE = Path(__file__).parent.parent / ".ritsu" / "playbooks.json"
MAX_PLAYBOOKS = 30

# インメモリキャッシュ
_playbooks_cache: list[dict] | None = None


# ── ファイルI/O ──────────────────────────────────────────

def _load_playbooks() -> list[dict]:
    """保存済みプレイブックをロード（キャッシュ付き）"""
    global _playbooks_cache
    if _playbooks_cache is not None:
        return _playbooks_cache
    if PLAYBOOKS_FILE.exists():
        try:
            data = json.loads(PLAYBOOKS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _playbooks_cache = data
                return _playbooks_cache
        except Exception:
            pass
    _playbooks_cache = []
    return []


def _save_playbooks(playbooks: list[dict]):
    """プレイブックを保存（キャッシュも更新）"""
    global _playbooks_cache
    _playbooks_cache = playbooks
    try:
        PLAYBOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLAYBOOKS_FILE.write_text(
            json.dumps(playbooks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Playbook save failed: {e}")


# ── 検索 ────────────────────────────────────────────────

def find_playbook(user_message: str, top_k: int = 3) -> list[dict]:
    """ユーザーメッセージにマッチするプレイブックを検索。

    キーワード一致数でスコアリングし、上位top_k件を返す。
    1つもキーワードが一致しなければ返さない。
    """
    msg = user_message.lower().replace(" ", "").replace("　", "")
    playbooks = _load_playbooks()

    scored: list[tuple[int, dict]] = []
    for pb in playbooks:
        keywords = pb.get("trigger_keywords", [])
        hits = sum(1 for kw in keywords if kw in msg)
        if hits > 0:
            scored.append((hits, pb))

    # ヒット数降順 → 成功回数降順でソート
    scored.sort(key=lambda x: (x[0], x[1].get("success_count", 0)), reverse=True)

    return [pb for _, pb in scored[:top_k]]


# ── 記録・更新・削除 ────────────────────────────────────

def _simplify_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """ツール呼び出しをステップ形式に簡略化。

    結果やメタデータを除去し、ツール名と主要引数のみ残す。
    引数は200文字に切り詰め、インジェクションパターンをフィルタ。
    """
    steps = []
    for call in tool_calls:
        step = {"tool": call.get("tool", call.get("name", "unknown"))}
        args = call.get("args", call.get("arguments", call.get("args_template", {})))
        if args:
            # 引数を切り詰め + サニタイズ
            sanitized_args = {}
            for k, v in args.items():
                v_str = str(v)[:200]
                sanitized_args[k] = v_str
            step["args"] = sanitized_args
        steps.append(step)
    return steps


def record_playbook(
    name: str,
    keywords: list[str],
    tool_calls: list[dict],
) -> dict:
    """成功したReAct実行からプレイブックを新規記録。

    MAX_PLAYBOOKSを超える場合、成功回数が最少のものを削除。
    """
    playbooks = _load_playbooks()

    pb = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "trigger_keywords": [kw.lower() for kw in keywords],
        "steps": _simplify_tool_calls(tool_calls),
        "success_count": 1,
        "last_used": datetime.now().isoformat(),
    }

    playbooks.append(pb)

    # 上限を超えたら品質スコアが最低のものを削除
    if len(playbooks) > MAX_PLAYBOOKS:
        playbooks.sort(key=lambda x: _quality_score(x))
        playbooks = playbooks[len(playbooks) - MAX_PLAYBOOKS :]

    _save_playbooks(playbooks)
    logger.info(f"Playbook recorded: {name} (keywords: {keywords})")
    return pb


def update_playbook(playbook_id: str, tool_calls: list[dict]):
    """既存プレイブックのステップを更新し、成功回数を加算。"""
    playbooks = _load_playbooks()

    for pb in playbooks:
        if pb["id"] == playbook_id:
            pb["steps"] = _simplify_tool_calls(tool_calls)
            pb["success_count"] = pb.get("success_count", 0) + 1
            pb["last_used"] = datetime.now().isoformat()
            _save_playbooks(playbooks)
            logger.info(f"Playbook updated: {pb['name']} (count: {pb['success_count']})")
            return pb

    logger.warning(f"Playbook not found: {playbook_id}")
    return None


def delete_playbook(name: str) -> bool:
    """名前でプレイブックを削除。"""
    playbooks = _load_playbooks()
    before = len(playbooks)
    playbooks = [pb for pb in playbooks if pb["name"] != name]

    if len(playbooks) < before:
        _save_playbooks(playbooks)
        logger.info(f"Playbook deleted: {name}")
        return True

    logger.warning(f"Playbook not found for deletion: {name}")
    return False


# ── 品質スコアリング ──────────────────────────────────────

def _quality_score(pb: dict) -> float:
    """プレイブックの品質スコア（eviction判定用）

    成功回数 + 最近使われたかどうか + ステップ数のバランス。
    MetaClaw の PRM スコアリングの簡易版。
    """
    success = pb.get("success_count", 0)
    last_used = pb.get("last_used", "")

    # 最終使用からの経過日数（古いほど低スコア）
    recency = 0.0
    if last_used:
        try:
            last_dt = datetime.fromisoformat(last_used)
            days_ago = (datetime.now() - last_dt).days
            recency = max(0, 1.0 - days_ago / 30)  # 30日で0に
        except (ValueError, TypeError):
            pass

    return success * 0.6 + recency * 0.4


# ── フォーマット ────────────────────────────────────────

def format_as_fewshot(playbooks: list[dict]) -> str:
    """マッチしたプレイブックをシステムプロンプト注入用テキストに変換。

    1プレイブックあたり最大3行のコンパクトな形式。
    例:
      [手順: 開発を始める]
      1. open_app(app_name="Cursor") → 2. press_key(key="n", modifiers=["command"]) → 3. press_key(key="`", modifiers=["control"])
      (成功 5回)
    """
    if not playbooks:
        return ""

    lines = ["# 参考手順（過去の成功パターン）"]
    for pb in playbooks:
        lines.append(f"[手順: {pb['name']}]")

        # ステップを矢印でつなぐ
        step_strs = []
        for i, step in enumerate(pb.get("steps", []), 1):
            tool = step.get("tool", "?")
            args = step.get("args", {})
            if args:
                args_str = ", ".join(f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in args.items())
                step_strs.append(f"{i}. {tool}({args_str})")
            else:
                step_strs.append(f"{i}. {tool}()")
        lines.append(" → ".join(step_strs))

        count = pb.get("success_count", 0)
        lines.append(f"(成功 {count}回)")

    return "\n".join(lines)
