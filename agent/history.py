"""会話履歴・セッション管理

会話履歴のロード/保存、Gemini contentsフォーマット変換、
スクラッチパッド（中間状態保存）、セッション自動保存を管理。
"""

import json
import logging
import time
from pathlib import Path

from llm.types import ContentPart

from memory.manager import memory
from memory.summarizer import generate_session_summary, extract_learnings
from agent.skill_evolver import evolve_from_session, evolve_from_failures, prune_skills

logger = logging.getLogger("shiki.agent")

# === 会話履歴 ===
_conversation_history: list[dict] = []
MAX_HISTORY = 20
SUMMARY_THRESHOLD = 10
_message_count_since_summary = 0
_HISTORY_FILE = Path(__file__).parent.parent / ".ritsu" / "current_session.json"


def _load_history():
    global _conversation_history
    if _HISTORY_FILE.exists():
        try:
            _conversation_history = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
            logger.info(f"Loaded {len(_conversation_history)} history entries")
        except Exception:
            _conversation_history = []


def _save_history_to_file():
    try:
        _HISTORY_FILE.write_text(
            json.dumps(_conversation_history, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"History save failed: {e}")


_load_history()


async def add_to_history(role: str, text: str):
    global _message_count_since_summary
    _conversation_history.append({"role": role, "text": text})
    _message_count_since_summary += 1
    _save_history_to_file()

    if _message_count_since_summary >= SUMMARY_THRESHOLD * 2:
        await auto_save_session()
        _message_count_since_summary = 0

    # O(1)のスライス代入（pop(0)はO(n)で遅い）
    if len(_conversation_history) > MAX_HISTORY * 2:
        _conversation_history[:] = _conversation_history[-(MAX_HISTORY * 2):]


def build_history_contents() -> list:
    """会話履歴をプロバイダー非依存のメッセージ形式に変換"""
    contents = []
    history_slice = _conversation_history[-MAX_HISTORY:]
    total = len(history_slice)
    for i, entry in enumerate(history_slice):
        role = "user" if entry["role"] == "user" else "assistant"
        if i >= total - 5:
            text = entry["text"][:2000]
        else:
            text = entry["text"][:500]
        contents.append({
            "role": role,
            "parts": [ContentPart(text=text)],
        })
    return contents


# === 学習結果の保存 ===

def save_learnings(learnings: dict):
    """抽出した学びをトピック別に保存（重複チェック付き）"""
    topic_config = {
        "preferences": "# オーナーの好み",
        "facts": "# オーナーについて",
        "schedule": "# オーナーの予定",
    }
    for key, header in topic_config.items():
        items = learnings.get(key)
        if not items:
            continue
        existing = memory.get_topic(key)
        if existing:
            existing_lower = existing.lower()
            new_items = [item for item in items if item.lower() not in existing_lower]
        else:
            new_items = items
        if not new_items:
            continue
        new_lines = "\n".join(f"- {item}" for item in new_items)
        if existing:
            memory.save_topic(key, f"{existing}\n{new_lines}")
        else:
            memory.save_topic(key, f"{header}\n\n{new_lines}")
        logger.info(f"Saved {len(new_items)} {key} learnings (deduped from {len(items)})")


# === セッション中のツール実行記録（スキル進化用） ===
_session_tool_calls: list[dict] = []


def record_tool_call(tool_name: str, tool_args: dict, success: bool):
    """ツール実行をセッション内で記録（スキル進化の材料）"""
    _session_tool_calls.append({
        "tool": tool_name,
        "args": {k: str(v)[:100] for k, v in tool_args.items()} if tool_args else {},
        "success": success,
    })


async def auto_save_session():
    global _session_tool_calls
    if len(_conversation_history) < 4:
        return
    try:
        summary = await generate_session_summary(_conversation_history)
        if summary:
            memory.save_session_summary(summary)
        learnings = await extract_learnings(_conversation_history)
        save_learnings(learnings)

        # スキル自動進化（MetaClaw inspired）
        if _session_tool_calls:
            try:
                await evolve_from_session(_conversation_history, _session_tool_calls)
            except Exception as e:
                logger.warning(f"Skill evolution from session failed: {e}")
            _session_tool_calls = []

        # 失敗パターンからもスキル進化
        if len(_failure_patterns) >= 3:
            try:
                await evolve_from_failures(_failure_patterns)
            except Exception as e:
                logger.warning(f"Skill evolution from failures failed: {e}")

        # 定期プルーニング
        prune_skills()

        # Tiered Memoryメンテナンス（降格・アーカイブ）
        try:
            from memory.tiered_memory import run_maintenance
            run_maintenance()
        except Exception as e:
            logger.warning(f"Tiered memory maintenance failed: {e}")

        # WALローテーション
        try:
            from agent.wal import wal_rotate
            wal_rotate()
        except Exception as e:
            logger.warning(f"WAL rotation failed: {e}")

        logger.info("Auto session save completed (with skill evolution)")
    except Exception as e:
        logger.error(f"Auto session save failed: {e}")


# === タスク計画 + スクラッチパッド（Manus AI todo.md方式） ===

_PLAN_FILE = Path(__file__).parent.parent / ".ritsu" / "todo.md"
_SCRATCHPAD_FILE = Path(__file__).parent.parent / ".ritsu" / "scratchpad.md"
_SCRATCHPAD_UPDATE_INTERVAL = 5

# AIが書き込む計画の上限
_MAX_PLAN_SIZE = 2000


async def update_plan(plan: str) -> dict:
    """AIが自分のタスク計画を書き込む（Manus todo.md方式）

    複雑なタスクの開始時にAIが計画を立て、進捗に応じて更新する。
    コンテキストウィンドウが切れても、このファイルが残る。

    Args:
        plan: マークダウン形式の計画テキスト
    """
    if not plan or not plan.strip():
        return {"success": False, "error": "計画が空"}

    # サイズ制限
    plan = plan[:_MAX_PLAN_SIZE]

    try:
        _PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PLAN_FILE.write_text(plan, encoding="utf-8")
        logger.info(f"Plan updated: {len(plan)} chars")
        return {"success": True, "output": "計画を更新した"}
    except Exception as e:
        logger.warning(f"Plan update failed: {e}")
        return {"success": False, "error": str(e)}


def load_plan() -> str:
    """現在の計画を読み込み（セッション復帰時に注入）"""
    if _PLAN_FILE.exists():
        try:
            return _PLAN_FILE.read_text(encoding="utf-8")[:_MAX_PLAN_SIZE]
        except Exception:
            pass
    return ""


def clear_plan():
    """タスク完了時に計画をクリア"""
    try:
        if _PLAN_FILE.exists():
            _PLAN_FILE.unlink()
    except Exception:
        pass


def update_scratchpad(user_message: str, iteration: int, tool_calls: list[dict], last_result: str):
    """スクラッチパッドを更新（長タスクの中間状態保存）"""
    try:
        steps = "\n".join(
            f"  {i+1}. {tc['tool']}({', '.join(f'{k}={v}' for k,v in tc.get('args',{}).items()) if tc.get('args') else ''})"
            for i, tc in enumerate(tool_calls[-10:])
        )
        content = (
            f"# 現在のタスク\n{user_message}\n\n"
            f"# 進捗: {iteration}イテレーション完了\n\n"
            f"# 実行済みステップ:\n{steps}\n\n"
            f"# 最新の結果:\n{last_result[:500]}\n"
        )
        _SCRATCHPAD_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SCRATCHPAD_FILE.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Scratchpad update failed: {e}")


def load_scratchpad() -> str:
    """スクラッチパッドを読み込み（前回の中間状態）"""
    if _SCRATCHPAD_FILE.exists():
        try:
            return _SCRATCHPAD_FILE.read_text(encoding="utf-8")
        except Exception:
            pass
    return ""


def clear_scratchpad():
    """タスク完了時にスクラッチパッドをクリア"""
    try:
        if _SCRATCHPAD_FILE.exists():
            _SCRATCHPAD_FILE.unlink()
    except Exception:
        pass


# === コンテキスト圧縮 ===

COMPRESS_AFTER_ITERATIONS = 8


def compress_old_screenshots(contents: list, keep_recent: int = 4) -> list:
    """古いスクショ（画像バイナリ）を要約テキストに置換してコンテキストを圧縮"""
    image_indices = []
    for i, content in enumerate(contents):
        if content.parts:
            for part in content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_indices.append(i)
                    break

    if len(image_indices) <= keep_recent:
        return contents

    indices_to_compress = set(image_indices[:-keep_recent])

    compressed = []
    for i, content in enumerate(contents):
        if i in indices_to_compress:
            new_parts = []
            parts = content.get("parts", []) if isinstance(content, dict) else getattr(content, "parts", [])
            for part in parts:
                if isinstance(part, ContentPart) and part.image_bytes:
                    new_parts.append(ContentPart(text="[スクショ省略]"))
                elif hasattr(part, 'inline_data') and part.inline_data:
                    new_parts.append(ContentPart(text="[スクショ省略]"))
                else:
                    new_parts.append(part)
            role = content.get("role", "user") if isinstance(content, dict) else getattr(content, "role", "user")
            compressed.append({"role": role, "parts": new_parts})
        else:
            compressed.append(content)

    logger.info(f"Context compressed: {len(indices_to_compress)} screenshots replaced with text")
    return compressed


# === 失敗パターン ===

_FAILURE_LOG_FILE = Path(__file__).parent.parent / ".ritsu" / "failure_log.json"
_failure_patterns: list[dict] = []


def _load_failure_patterns():
    global _failure_patterns
    if _FAILURE_LOG_FILE.exists():
        try:
            _failure_patterns = json.loads(_FAILURE_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            _failure_patterns = []


def record_failure(tool_name: str, tool_args: dict, error: str):
    _failure_patterns.append({
        "tool": tool_name,
        "args_summary": str(tool_args)[:200],
        "error": str(error)[:200],
        "time": time.time(),
    })
    if len(_failure_patterns) > 100:
        _failure_patterns[:] = _failure_patterns[-100:]
    try:
        _FAILURE_LOG_FILE.write_text(
            json.dumps(_failure_patterns, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"Failure log write failed: {e}")


def get_failure_patterns() -> list[dict]:
    return _failure_patterns


_load_failure_patterns()
