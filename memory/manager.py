"""記憶管理 - 識の長期記憶

会話 → セッション要約 → 日次要約 → トピック抽出
全てMarkdownファイルベース。人間が読める。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from config import (
    RITSU_DIR, SOUL_PATH, MEMORY_PATH,
    TOPICS_DIR, DAILY_DIR, SESSIONS_DIR,
)

logger = logging.getLogger("shiki.memory")


class MemoryManager:
    """記憶の読み書きを一元管理"""

    def __init__(self):
        # ディレクトリ確保
        for d in [TOPICS_DIR, DAILY_DIR, SESSIONS_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        # セッションカウンター
        self._session_count = self._count_today_sessions() + 1
        self._session_id = self._make_session_id()
        logger.info(f"Memory session: {self._session_id}")

    def _count_today_sessions(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        return len(list(SESSIONS_DIR.glob(f"{today}-*.md")))

    def _make_session_id(self) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return f"{today}-{self._session_count:03d}"

    # === セッション要約 ===

    def save_session_summary(self, summary: str):
        """セッション要約を保存"""
        filepath = SESSIONS_DIR / f"{self._session_id}.md"
        content = (
            f"# セッション {self._session_id}\n"
            f"時刻: {datetime.now().strftime('%H:%M')}\n\n"
            f"{summary}\n"
        )
        try:
            filepath.write_text(content, encoding="utf-8")
            logger.info(f"Session summary saved: {filepath}")
        except Exception as e:
            logger.error(f"Session summary save failed: {e}")

    # === 日次要約 ===

    def get_today_sessions(self) -> list[str]:
        """今日のセッション要約を全て取得"""
        today = datetime.now().strftime("%Y-%m-%d")
        sessions = []
        for f in sorted(SESSIONS_DIR.glob(f"{today}-*.md")):
            sessions.append(f.read_text(encoding="utf-8"))
        return sessions

    def save_daily_summary(self, summary: str):
        """日次要約を保存"""
        today = datetime.now().strftime("%Y-%m-%d")
        filepath = DAILY_DIR / f"{today}.md"
        try:
            filepath.write_text(summary, encoding="utf-8")
            logger.info(f"Daily summary saved: {filepath}")
        except Exception as e:
            logger.error(f"Daily summary save failed: {e}")

    # === トピック管理 ===

    def get_topic(self, name: str) -> str:
        """トピックファイルを読む"""
        filepath = TOPICS_DIR / f"{name}.md"
        if filepath.exists():
            return filepath.read_text(encoding="utf-8")
        return ""

    def save_topic(self, name: str, content: str):
        """トピックファイルを保存"""
        filepath = TOPICS_DIR / f"{name}.md"
        try:
            filepath.write_text(content, encoding="utf-8")
            logger.info(f"Topic saved: {name}")
            self._update_memory_index()
        except Exception as e:
            logger.error(f"Topic save failed ({name}): {e}")

    def list_topics(self) -> list[str]:
        """全トピック名を取得"""
        return [f.stem for f in sorted(TOPICS_DIR.glob("*.md"))]

    # === MEMORY.md インデックス ===

    def _update_memory_index(self):
        """MEMORY.mdのインデックスを更新"""
        topics = self.list_topics()
        lines = ["# 識（しき）長期記憶インデックス", ""]

        if topics:
            lines.append("## トピック")
            for topic in topics:
                filepath = TOPICS_DIR / f"{topic}.md"
                # 1行目をタイトルとして取得
                first_line = filepath.read_text(encoding="utf-8").split("\n")[0]
                first_line = first_line.lstrip("# ").strip()
                lines.append(f"- [{topic}] {first_line}")
        else:
            lines.append("まだ何も覚えていない。")

        # 最近の日次要約
        daily_files = sorted(DAILY_DIR.glob("*.md"), reverse=True)[:3]
        if daily_files:
            lines.append("")
            lines.append("## 最近の日次要約")
            for f in daily_files:
                lines.append(f"- {f.stem}")

        try:
            MEMORY_PATH.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.error(f"Memory index update failed: {e}")

    # === SOUL.md 更新 ===

    def update_soul(self, section: str, content: str):
        """SOUL.mdの特定セクションを更新"""
        if not SOUL_PATH.exists():
            return

        soul = SOUL_PATH.read_text(encoding="utf-8")

        # 「成長する要素」セクションを探して更新
        if section == "learned":
            old = "- オーナーとの会話から学んだこと: （まだ何も知らない...これから学ぶ）"
            if old in soul:
                soul = soul.replace(old, f"- オーナーとの会話から学んだこと:\n{content}")
            else:
                # 既に学習済みの場合、追記
                marker = "- オーナーとの会話から学んだこと:"
                if marker in soul:
                    idx = soul.index(marker)
                    # 次の「-」まで or 末尾
                    next_dash = soul.find("\n-", idx + len(marker))
                    if next_dash == -1:
                        next_dash = len(soul)
                    soul = soul[:next_dash] + f"\n  - {content}" + soul[next_dash:]

        elif section == "likes":
            old = "- 好きなもの・嫌いなもの: （これから知っていく）"
            if old in soul:
                soul = soul.replace(old, f"- 好きなもの・嫌いなもの:\n{content}")

        try:
            SOUL_PATH.write_text(soul, encoding="utf-8")
            logger.info(f"SOUL.md updated: {section}")
        except Exception as e:
            logger.error(f"SOUL.md update failed ({section}): {e}")


# グローバルインスタンス
memory = MemoryManager()
