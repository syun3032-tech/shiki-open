"""memory/manager.py のテスト"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def memory_dirs(tmp_path):
    """MemoryManagerが使うディレクトリ構造をtmp_pathに作成"""
    ritsu = tmp_path / ".ritsu"
    ritsu.mkdir()
    topics = ritsu / "topics"
    topics.mkdir()
    daily = ritsu / "daily"
    daily.mkdir()
    sessions = ritsu / "sessions"
    sessions.mkdir()
    memory_md = ritsu / "MEMORY.md"
    memory_md.write_text("# 長期記憶\n", encoding="utf-8")
    soul_md = ritsu / "SOUL.md"
    soul_md.write_text(
        "# 魂\n\n## 成長する要素\n"
        "- オーナーとの会話から学んだこと: （まだ何も知らない...これから学ぶ）\n"
        "- 好きなもの・嫌いなもの: （これから知っていく）\n",
        encoding="utf-8",
    )
    return {
        "ritsu": ritsu,
        "topics": topics,
        "daily": daily,
        "sessions": sessions,
        "memory_md": memory_md,
        "soul_md": soul_md,
    }


@pytest.fixture
def mm(memory_dirs):
    """パッチ済みのMemoryManagerを生成"""
    dirs = memory_dirs
    with patch("memory.manager.RITSU_DIR", dirs["ritsu"]), \
         patch("memory.manager.SOUL_PATH", dirs["soul_md"]), \
         patch("memory.manager.MEMORY_PATH", dirs["memory_md"]), \
         patch("memory.manager.TOPICS_DIR", dirs["topics"]), \
         patch("memory.manager.DAILY_DIR", dirs["daily"]), \
         patch("memory.manager.SESSIONS_DIR", dirs["sessions"]):
        from memory.manager import MemoryManager
        mgr = MemoryManager()
        yield mgr


class TestSessionId:
    """セッションID生成のテスト"""

    def test_session_id_format(self, mm):
        # セッションIDは "YYYY-MM-DD-NNN" 形式
        sid = mm._session_id
        parts = sid.rsplit("-", 1)
        assert len(parts) == 2
        assert parts[1].isdigit()
        assert len(parts[1]) == 3  # 3桁ゼロ埋め


class TestSessionSummary:
    """セッション要約のsave/loadテスト"""

    def test_save_and_load(self, mm, memory_dirs):
        mm.save_session_summary("テストセッション要約")
        session_files = list(memory_dirs["sessions"].glob("*.md"))
        assert len(session_files) >= 1
        content = session_files[0].read_text(encoding="utf-8")
        assert "テストセッション要約" in content


class TestTopicManagement:
    """トピック管理のテスト"""

    def test_save_and_get_topic(self, mm):
        mm.save_topic("test_topic", "# テストトピック\nこれはテスト")
        result = mm.get_topic("test_topic")
        assert "テストトピック" in result

    def test_get_nonexistent_topic_returns_empty(self, mm):
        result = mm.get_topic("nonexistent")
        assert result == ""

    def test_list_topics(self, mm):
        mm.save_topic("alpha", "# Alpha")
        mm.save_topic("beta", "# Beta")
        topics = mm.list_topics()
        assert "alpha" in topics
        assert "beta" in topics

    def test_list_topics_empty(self, mm):
        topics = mm.list_topics()
        assert topics == []


class TestMemoryIndex:
    """MEMORY.md インデックス更新のテスト"""

    def test_update_after_topic_save(self, mm, memory_dirs):
        mm.save_topic("coding", "# コーディング技術\n学んだこと")
        index = memory_dirs["memory_md"].read_text(encoding="utf-8")
        assert "コーディング技術" in index
        assert "coding" in index

    def test_empty_index(self, mm, memory_dirs):
        mm._update_memory_index()
        index = memory_dirs["memory_md"].read_text(encoding="utf-8")
        assert "まだ何も覚えていない" in index


class TestSoulUpdate:
    """SOUL.md更新のテスト"""

    def test_update_learned_section(self, mm, memory_dirs):
        mm.update_soul("learned", "  - Pythonが得意")
        soul = memory_dirs["soul_md"].read_text(encoding="utf-8")
        assert "Pythonが得意" in soul
        # 元のプレースホルダが消えている
        assert "まだ何も知らない" not in soul

    def test_update_likes_section(self, mm, memory_dirs):
        mm.update_soul("likes", "  - コーヒーが好き")
        soul = memory_dirs["soul_md"].read_text(encoding="utf-8")
        assert "コーヒーが好き" in soul
        assert "これから知っていく" not in soul
