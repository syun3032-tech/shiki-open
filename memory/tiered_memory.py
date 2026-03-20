"""Tiered Memory System — OpenClaw self-improving inspired

HOT/WARM/COLD 3層メモリ:
- HOT:  毎回システムプロンプトに注入（最重要、max 30件）
- WARM: キーワードマッチ時のみ注入（max 100件）
- COLD: アーカイブ（直接クエリ時のみ、max 300件）

昇格/降格ルール:
- access_count >= 3 → HOT昇格
- 30日未アクセス → 1段階降格
- COLD + 60日未アクセス → 削除

既存の memory/manager.py とは独立。
manager.py = 物語的知識（日記、セッション要約）
tiered_memory.py = 行動規則（訂正、好み、ルール）
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from config import RITSU_DIR

logger = logging.getLogger("shiki.memory")

_MEMORY_FILE = RITSU_DIR / "tiered_memory.json"

# 制限
MAX_HOT = 30
MAX_WARM = 100
MAX_COLD = 300


def _load() -> list[dict]:
    """メモリをロード"""
    if _MEMORY_FILE.exists():
        try:
            return json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(memories: list[dict]):
    """メモリを保存"""
    try:
        _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MEMORY_FILE.write_text(
            json.dumps(memories, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Tiered memory save failed: {e}")


def add_memory(
    content: str,
    wrong_behavior: str = "",
    source: str = "correction",
) -> str:
    """新しいメモリを追加（WARM tier から開始）

    Returns: memory ID
    """
    memories = _load()

    # 重複チェック（同じ内容が既にあれば access_count を増やすだけ）
    content_lower = content.lower()
    for mem in memories:
        if mem["content"].lower() == content_lower:
            mem["access_count"] += 1
            mem["last_accessed"] = datetime.now().isoformat()
            _check_promotion(mem)
            _save(memories)
            logger.info(f"Tiered memory deduped: {content[:50]} (count={mem['access_count']})")
            return mem["id"]

    mem_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    entry = {
        "id": mem_id,
        "content": content[:500],
        "wrong_behavior": wrong_behavior[:300],
        "source": source,
        "tier": "WARM",
        "access_count": 1,
        "last_accessed": now,
        "created_at": now,
    }

    memories.append(entry)

    # WARM 上限チェック（古いものから削除）
    warm = [m for m in memories if m["tier"] == "WARM"]
    if len(warm) > MAX_WARM:
        oldest = min(warm, key=lambda m: m["last_accessed"])
        memories = [m for m in memories if m["id"] != oldest["id"]]

    _save(memories)
    logger.info(f"Tiered memory added: [{source}] {content[:50]}")
    return mem_id


def get_hot_memories() -> list[dict]:
    """HOT tier のメモリを全取得（システムプロンプト注入用）"""
    memories = _load()
    return [m for m in memories if m["tier"] == "HOT"]


def get_warm_memories(keywords: list[str]) -> list[dict]:
    """キーワードに関連する WARM メモリを取得"""
    if not keywords:
        return []

    memories = _load()
    warm = [m for m in memories if m["tier"] == "WARM"]

    results = []
    for mem in warm:
        content_lower = mem["content"].lower()
        if any(kw.lower() in content_lower for kw in keywords):
            results.append(mem)
            # アクセス記録
            mem["access_count"] += 1
            mem["last_accessed"] = datetime.now().isoformat()
            _check_promotion(mem)

    if results:
        _save(memories)

    return results[:10]  # 最大10件


def _check_promotion(mem: dict):
    """昇格チェック: access_count >= 3 → HOT"""
    if mem["tier"] == "WARM" and mem["access_count"] >= 3:
        # HOT 上限チェック
        memories = _load()
        hot_count = sum(1 for m in memories if m["tier"] == "HOT")
        if hot_count < MAX_HOT:
            mem["tier"] = "HOT"
            logger.info(f"Memory promoted to HOT: {mem['content'][:50]}")


def run_maintenance():
    """定期メンテナンス: 降格 + 削除（auto_save_session から呼ぶ）"""
    memories = _load()
    if not memories:
        return

    now = datetime.now()
    changed = False

    for mem in memories[:]:  # コピーをイテレート
        last = datetime.fromisoformat(mem["last_accessed"])
        days_idle = (now - last).days

        if mem["tier"] == "HOT" and days_idle >= 30:
            mem["tier"] = "WARM"
            logger.info(f"Memory demoted HOT→WARM: {mem['content'][:50]}")
            changed = True

        elif mem["tier"] == "WARM" and days_idle >= 30:
            mem["tier"] = "COLD"
            logger.info(f"Memory demoted WARM→COLD: {mem['content'][:50]}")
            changed = True

        elif mem["tier"] == "COLD" and days_idle >= 60:
            memories.remove(mem)
            logger.info(f"Memory archived (removed): {mem['content'][:50]}")
            changed = True

    # COLD 上限チェック
    cold = [m for m in memories if m["tier"] == "COLD"]
    if len(cold) > MAX_COLD:
        cold_sorted = sorted(cold, key=lambda m: m["last_accessed"])
        to_remove = cold_sorted[:len(cold) - MAX_COLD]
        for m in to_remove:
            memories.remove(m)
            changed = True

    if changed:
        _save(memories)


def format_hot_for_prompt(hot_memories: list[dict]) -> str:
    """HOT メモリをシステムプロンプト用にフォーマット"""
    if not hot_memories:
        return ""

    lines = []
    for mem in hot_memories:
        source_icon = {"correction": "!", "preference": "*", "fact": "-"}.get(
            mem.get("source", ""), "-"
        )
        lines.append(f"{source_icon} {mem['content']}")

    return "# 重要な学習事項（オーナーからの指摘）\n" + "\n".join(lines)


def get_stats() -> dict:
    """メモリ統計（ヘルスチェック用）"""
    memories = _load()
    return {
        "total": len(memories),
        "hot": sum(1 for m in memories if m["tier"] == "HOT"),
        "warm": sum(1 for m in memories if m["tier"] == "WARM"),
        "cold": sum(1 for m in memories if m["tier"] == "COLD"),
    }
