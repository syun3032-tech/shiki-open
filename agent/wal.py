"""Write-Ahead Log (WAL) — OpenClaw elite-longterm-memory inspired

応答前に状態をディスクに書き込む。
クラッシュしても「何をやっていたか」がわかる。

- JSONL形式（1行1エントリ、append-only）
- 完了時にtruncate
- 7日以上前のWALは自動ローテート
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import RITSU_DIR

logger = logging.getLogger("shiki.agent")

_WAL_FILE = RITSU_DIR / "wal.jsonl"
_WAL_ARCHIVE_DIR = RITSU_DIR / "wal_archive"
_seq = 0


def wal_write(phase: str, **kwargs):
    """WALエントリを書き込み（fsync付き）

    phase: "task_start" | "pre_llm" | "post_llm" |
           "pre_tool" | "post_tool" | "completed"
    """
    global _seq
    _seq += 1

    entry = {
        "seq": _seq,
        "ts": datetime.now().isoformat(),
        "phase": phase,
    }
    entry.update(kwargs)

    # 大きな値を切り詰め
    for key in ("tool_args", "result_summary", "user_message"):
        if key in entry and isinstance(entry[key], str) and len(entry[key]) > 200:
            entry[key] = entry[key][:200]

    try:
        _WAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_WAL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        # WAL書き込み失敗はクリティカルだが、メイン処理を止めてはいけない
        logger.error(f"WAL write failed: {e}")


def wal_complete():
    """タスク完了 → WAL をクリア"""
    global _seq
    _seq = 0
    try:
        if _WAL_FILE.exists():
            _WAL_FILE.unlink()
    except Exception as e:
        logger.error(f"WAL clear failed: {e}")


def wal_recover() -> dict | None:
    """起動時: 未完了タスクがあれば復旧情報を返す

    Returns:
        {"user_message": "...", "last_phase": "...", "iteration": N,
         "tool_calls": [...], "elapsed_entries": N}
        or None if no incomplete task.
    """
    if not _WAL_FILE.exists():
        return None

    try:
        entries = []
        with open(_WAL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))

        if not entries:
            return None

        # 最後のエントリが "completed" なら → 正常終了、WALを消す
        if entries[-1]["phase"] == "completed":
            wal_complete()
            return None

        # 未完了タスクを検出
        user_message = ""
        last_phase = entries[-1]["phase"]
        iteration = 0
        tool_calls = []

        for e in entries:
            if e["phase"] == "task_start" and "user_message" in e:
                user_message = e["user_message"]
            if "iteration" in e:
                iteration = max(iteration, e["iteration"])
            if e["phase"] == "post_tool" and "tool_name" in e:
                tool_calls.append(e["tool_name"])

        # WALが古すぎる場合（1時間以上前）は無視
        first_ts = entries[0].get("ts", "")
        if first_ts:
            try:
                first_time = datetime.fromisoformat(first_ts)
                if datetime.now() - first_time > timedelta(hours=1):
                    logger.info("WAL too old (>1h), discarding")
                    wal_complete()
                    return None
            except Exception:
                pass

        logger.warning(
            f"WAL recovery: '{user_message[:50]}' "
            f"phase={last_phase}, iter={iteration}, tools={len(tool_calls)}"
        )

        return {
            "user_message": user_message,
            "last_phase": last_phase,
            "iteration": iteration,
            "tool_calls": tool_calls,
            "elapsed_entries": len(entries),
        }

    except Exception as e:
        logger.error(f"WAL recovery failed: {e}")
        return None


def wal_rotate():
    """古い WAL アーカイブを削除（7日以上前）"""
    try:
        if not _WAL_ARCHIVE_DIR.exists():
            return

        cutoff = time.time() - 7 * 86400
        for f in _WAL_ARCHIVE_DIR.glob("*.jsonl"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                logger.info(f"WAL archive rotated: {f.name}")
    except Exception as e:
        logger.error(f"WAL rotation failed: {e}")
