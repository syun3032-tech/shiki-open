"""llm/types.py のテスト"""
import sys
from pathlib import Path

# プロジェクトルートをsys.pathに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.types import LLMResponse, ContentPart, ToolCall, LLMConfig, ToolDefinition


class TestToolCall:
    """ToolCallデータクラスのテスト"""

    def test_creation_with_defaults(self):
        tc = ToolCall(name="click", args={"x": 100, "y": 200})
        assert tc.name == "click"
        assert tc.args == {"x": 100, "y": 200}
        assert tc.id == ""

    def test_creation_with_id(self):
        tc = ToolCall(name="read_file", args={"path": "/tmp"}, id="call_123")
        assert tc.id == "call_123"


class TestContentPart:
    """ContentPartデータクラスのテスト"""

    def test_text_part(self):
        p = ContentPart(text="こんにちは")
        assert p.text == "こんにちは"
        assert p.tool_call is None
        assert p.image_bytes is None

    def test_image_part(self):
        p = ContentPart(image_bytes=b"\x89PNG", mime_type="image/png")
        assert p.image_bytes == b"\x89PNG"
        assert p.mime_type == "image/png"
        assert p.text is None

    def test_tool_call_part(self):
        tc = ToolCall(name="click", args={"x": 10, "y": 20})
        p = ContentPart(tool_call=tc)
        assert p.tool_call is tc
        assert p.text is None

    def test_default_mime_type(self):
        p = ContentPart()
        assert p.mime_type == "image/jpeg"


class TestLLMResponse:
    """LLMResponseのプロパティテスト"""

    def test_text_single_part(self):
        resp = LLMResponse(parts=[ContentPart(text="Hello")])
        assert resp.text == "Hello"

    def test_text_multiple_parts(self):
        # 複数テキストパートは改行で結合される
        resp = LLMResponse(parts=[
            ContentPart(text="Line1"),
            ContentPart(text="Line2"),
        ])
        assert resp.text == "Line1\nLine2"

    def test_text_skips_non_text_parts(self):
        resp = LLMResponse(parts=[
            ContentPart(text="テキスト"),
            ContentPart(image_bytes=b"img"),
        ])
        assert resp.text == "テキスト"

    def test_text_empty_when_no_text_parts(self):
        resp = LLMResponse(parts=[ContentPart(image_bytes=b"img")])
        assert resp.text == ""

    def test_tool_calls_property(self):
        tc1 = ToolCall(name="click", args={})
        tc2 = ToolCall(name="type_text", args={"text": "hi"})
        resp = LLMResponse(parts=[
            ContentPart(tool_call=tc1),
            ContentPart(text="テキスト"),
            ContentPart(tool_call=tc2),
        ])
        assert resp.tool_calls == [tc1, tc2]

    def test_tool_calls_empty(self):
        resp = LLMResponse(parts=[ContentPart(text="テキストだけ")])
        assert resp.tool_calls == []

    def test_has_tool_calls_true(self):
        tc = ToolCall(name="click", args={})
        resp = LLMResponse(parts=[ContentPart(tool_call=tc)])
        assert resp.has_tool_calls is True

    def test_has_tool_calls_false(self):
        resp = LLMResponse(parts=[ContentPart(text="テキスト")])
        assert resp.has_tool_calls is False

    def test_finish_reason_default(self):
        resp = LLMResponse()
        assert resp.finish_reason == ""

    def test_raw_default(self):
        resp = LLMResponse()
        assert resp.raw is None


class TestLLMConfig:
    """LLMConfigデフォルト値のテスト"""

    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.model == ""
        assert cfg.temperature == 0.0
        assert cfg.max_tokens == 2048
        assert cfg.system_prompt == ""
        assert cfg.tools == []
        assert cfg.thinking_budget is None

    def test_custom_values(self):
        cfg = LLMConfig(
            model="gemini-2.5-pro",
            temperature=0.7,
            max_tokens=4096,
        )
        assert cfg.model == "gemini-2.5-pro"
        assert cfg.temperature == 0.7
        assert cfg.max_tokens == 4096


class TestToolDefinition:
    """ToolDefinitionのテスト"""

    def test_creation(self):
        td = ToolDefinition(
            name="test_tool",
            description="テスト用ツール",
            parameters={"type": "object", "properties": {}},
        )
        assert td.name == "test_tool"
        assert td.description == "テスト用ツール"
        assert td.parameters["type"] == "object"
