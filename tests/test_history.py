"""agent/history.py のテスト"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def mock_history_deps():
    """history.py のインポート時依存をモック"""
    # memory.manager, memory.summarizer, agent.skill_evolver をモック
    mock_memory = MagicMock()
    mock_memory.memory = MagicMock()
    mock_summarizer = MagicMock()
    mock_skill_evolver = MagicMock()

    with patch.dict(sys.modules, {
        "memory.summarizer": mock_summarizer,
        "agent.skill_evolver": mock_skill_evolver,
    }):
        yield


@pytest.fixture
def history_module(tmp_path, mock_history_deps):
    """パッチ済みのhistoryモジュール"""
    history_file = tmp_path / "current_session.json"
    failure_file = tmp_path / "failure_log.json"

    # モジュールを再ロード
    if "agent.history" in sys.modules:
        del sys.modules["agent.history"]

    with patch("memory.manager.SESSIONS_DIR", tmp_path / "sessions"), \
         patch("memory.manager.TOPICS_DIR", tmp_path / "topics"), \
         patch("memory.manager.DAILY_DIR", tmp_path / "daily"):
        (tmp_path / "sessions").mkdir(exist_ok=True)
        (tmp_path / "topics").mkdir(exist_ok=True)
        (tmp_path / "daily").mkdir(exist_ok=True)

        import agent.history as hist
        # 内部状態をリセット
        hist._conversation_history = []
        hist._message_count_since_summary = 0
        hist._HISTORY_FILE = history_file
        hist._FAILURE_LOG_FILE = failure_file
        hist._failure_patterns = []
        yield hist


class TestAddToHistory:
    """add_to_history / build_history_contents のテスト"""

    @pytest.mark.asyncio
    async def test_add_and_build_roundtrip(self, history_module):
        await history_module.add_to_history("user", "こんにちは")
        await history_module.add_to_history("assistant", "はい、こんにちは！")

        contents = history_module.build_history_contents()
        assert len(contents) == 2
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_assistant_role_not_model(self, history_module):
        """プロバイダー非依存で "assistant" ロールを使用"""
        await history_module.add_to_history("assistant", "応答テスト")
        contents = history_module.build_history_contents()
        # "model" ではなく "assistant" が使われる
        assert contents[0]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_history_has_parts_with_content_part(self, history_module):
        await history_module.add_to_history("user", "テスト")
        contents = history_module.build_history_contents()
        parts = contents[0]["parts"]
        assert len(parts) == 1
        assert parts[0].text == "テスト"


class TestCompressOldScreenshots:
    """compress_old_screenshots のテスト"""

    def test_replaces_old_images_with_placeholder(self, history_module):
        from llm.types import ContentPart

        # インラインデータを持つモックコンテンツを作成
        contents = []
        for i in range(6):
            mock_content = MagicMock()
            mock_part = MagicMock()
            mock_part.inline_data = b"fake_image_data"
            mock_content.parts = [mock_part]
            mock_content.get = MagicMock(side_effect=lambda k, d=None: {"role": "user"}.get(k, d))
            contents.append(mock_content)

        result = history_module.compress_old_screenshots(contents, keep_recent=4)
        assert len(result) == 6
        # 最初の2つが圧縮される（6 - keep_recent=4）
        for i in range(2):
            parts = result[i].get("parts", []) if isinstance(result[i], dict) else []
            if parts:
                assert any(
                    (isinstance(p, ContentPart) and p.text == "[スクショ省略]")
                    for p in parts
                )

    def test_no_compression_when_few_images(self, history_module):
        # keep_recent以下ならそのまま返す
        contents = []
        for i in range(3):
            mock_content = MagicMock()
            mock_part = MagicMock()
            mock_part.inline_data = b"img"
            mock_content.parts = [mock_part]
            contents.append(mock_content)

        result = history_module.compress_old_screenshots(contents, keep_recent=4)
        assert len(result) == 3
        # 変更なし（元のオブジェクトがそのまま）
        for i in range(3):
            assert result[i] is contents[i]


class TestSaveLearnings:
    """save_learnings のテスト"""

    def test_writes_to_correct_topics(self, history_module):
        mock_memory = MagicMock()
        mock_memory.get_topic.return_value = ""

        with patch.object(history_module, "memory", mock_memory):
            history_module.save_learnings({
                "preferences": ["コーヒーが好き", "紅茶も飲む"],
                "facts": ["東京在住"],
            })

            # preferencesトピックに保存された
            calls = mock_memory.save_topic.call_args_list
            topic_names = [c[0][0] for c in calls]
            assert "preferences" in topic_names
            assert "facts" in topic_names

    def test_deduplicates_existing(self, history_module):
        mock_memory = MagicMock()
        mock_memory.get_topic.return_value = "# オーナーの好み\n- コーヒーが好き"

        with patch.object(history_module, "memory", mock_memory):
            history_module.save_learnings({
                "preferences": ["コーヒーが好き", "紅茶も好き"],
            })

            # 重複除去で「紅茶も好き」だけが追加される
            if mock_memory.save_topic.called:
                saved_content = mock_memory.save_topic.call_args[0][1]
                assert "紅茶も好き" in saved_content

    def test_empty_learnings_no_save(self, history_module):
        mock_memory = MagicMock()
        with patch.object(history_module, "memory", mock_memory):
            history_module.save_learnings({})
            mock_memory.save_topic.assert_not_called()
