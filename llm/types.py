"""LLM統一型定義 — プロバイダー間の差異を吸収"""
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str = ""  # OpenAI/Claude need this for tool_result


@dataclass
class ContentPart:
    text: str | None = None
    tool_call: ToolCall | None = None
    image_bytes: bytes | None = None
    mime_type: str = "image/jpeg"


@dataclass
class LLMResponse:
    parts: list[ContentPart] = field(default_factory=list)
    finish_reason: str = ""  # "stop", "tool_use", "safety", "error"
    raw: object = None  # Provider-specific raw response

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if p.text)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [p.tool_call for p in self.parts if p.tool_call]

    @property
    def has_tool_calls(self) -> bool:
        return any(p.tool_call for p in self.parts)


@dataclass
class ToolDefinition:
    """Unified tool definition (provider-agnostic)"""
    name: str
    description: str
    parameters: dict  # JSON Schema format (OpenAI-style, most universal)


@dataclass
class LLMConfig:
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 2048
    system_prompt: str = ""
    tools: list[ToolDefinition] = field(default_factory=list)
    thinking_budget: int | None = None  # Gemini extended thinking
