"""共通フィクスチャ"""
import json
import pytest
from pathlib import Path


@pytest.fixture
def tmp_ritsu_dir(tmp_path):
    """.ritsuディレクトリの正しい構造を一時ディレクトリに作成"""
    ritsu = tmp_path / ".ritsu"
    ritsu.mkdir()
    (ritsu / "topics").mkdir()
    (ritsu / "daily").mkdir()
    (ritsu / "sessions").mkdir()
    (ritsu / "activity").mkdir()

    # SOUL.md を作成
    soul_content = (
        "# 識（しき）の魂\n\n"
        "## 成長する要素\n"
        "- オーナーとの会話から学んだこと: （まだ何も知らない...これから学ぶ）\n"
        "- 好きなもの・嫌いなもの: （これから知っていく）\n"
    )
    (ritsu / "SOUL.md").write_text(soul_content, encoding="utf-8")

    # MEMORY.md を作成
    (ritsu / "MEMORY.md").write_text("# 長期記憶インデックス\n", encoding="utf-8")

    return ritsu


@pytest.fixture
def mock_gemini_response():
    """モックLLMレスポンスのファクトリ"""
    def _make(text: str = "テスト応答", finish_reason: str = "stop"):
        from llm.types import LLMResponse, ContentPart
        return LLMResponse(
            parts=[ContentPart(text=text)],
            finish_reason=finish_reason,
        )
    return _make


@pytest.fixture
def sample_config():
    """サンプルのuser_config辞書"""
    return {
        "owner_name": "テスト太郎",
        "owner_display_name": "太郎さん",
        "shiki_name": "識",
        "language": "ja",
        "allowed_apps": ["Google Chrome", "Finder"],
        "channels": {"line": False, "discord": False, "cli": True},
        "observation": {
            "enabled": True,
            "interval_seconds": 10,
            "learn_patterns": False,
        },
    }
