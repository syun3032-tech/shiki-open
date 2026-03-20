# Multi-Model Guide

Shiki supports multiple LLM providers through a unified abstraction layer. You can switch providers by changing a single environment variable.

## Supported Providers

### Gemini (Default)

Google's Gemini models. Recommended for the generous free tier and strong function calling support.

```bash
# .env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
```

- **Default model**: `gemini-2.5-pro`
- **Smart routing**: Automatically switches between `gemini-2.5-pro` (complex tasks) and `gemini-2.5-flash` (simple tasks)
- **Get API key**: [Google AI Studio](https://aistudio.google.com/) (free)

### OpenAI

GPT-4o and other OpenAI models.

```bash
# .env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o          # optional, default: gpt-4o
```

- **Default model**: `gpt-4o`
- **Other options**: `gpt-4o-mini`, `gpt-4-turbo`, `o1`, `o3`
- **Get API key**: [platform.openai.com](https://platform.openai.com/api-keys)

### Anthropic (Claude)

Claude models from Anthropic.

```bash
# .env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6    # optional
```

- **Default model**: `claude-sonnet-4-6`
- **Other options**: `claude-opus-4-6`, `claude-haiku-3-5`
- **Get API key**: [console.anthropic.com](https://console.anthropic.com/)

### Ollama (Local)

Run models locally with no API costs. Requires [Ollama](https://ollama.com/) installed and running.

```bash
# .env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1    # optional, this is the default
OLLAMA_MODEL=llama3.1                         # optional, default: llama3.1
```

- **Default model**: `llama3.1`
- **Other options**: Any model available via `ollama pull` (e.g., `mistral`, `codellama`, `gemma2`)
- **Setup**: Install Ollama, then `ollama pull llama3.1`

## Smart Routing

When using Gemini, Shiki automatically routes between Pro and Flash models based on task complexity:

**Flash (fast, cheap)** is used for:
- Simple questions and greetings
- Screenshot requests
- Volume/display changes
- Reminder queries

**Pro (capable)** is used for:
- Multi-step tasks
- Code writing and debugging
- Research and analysis
- File operations with judgment

For other providers, routing is not currently implemented -- all tasks use the configured model.

## Cost Comparison

Approximate costs per 1M tokens (as of early 2026):

| Provider | Model | Input | Output |
|----------|-------|-------|--------|
| Gemini | gemini-2.5-flash | Free tier available | Free tier available |
| Gemini | gemini-2.5-pro | Free tier available | Free tier available |
| OpenAI | gpt-4o | ~$2.50 | ~$10.00 |
| OpenAI | gpt-4o-mini | ~$0.15 | ~$0.60 |
| Anthropic | claude-sonnet-4-6 | ~$3.00 | ~$15.00 |
| Anthropic | claude-haiku-3-5 | ~$0.80 | ~$4.00 |
| Ollama | any | Free (local compute) | Free (local compute) |

Shiki enforces a daily cost cap (`MAX_COST_PER_DAY` in config.py, default $5.00) to prevent unexpected bills.

## Limitations by Provider

| Feature | Gemini | OpenAI | Anthropic | Ollama |
|---------|--------|--------|-----------|--------|
| Function calling | Full | Full | Full | Partial (model-dependent) |
| Vision (screenshots) | Yes | Yes | Yes | Model-dependent |
| Smart routing | Yes (Pro/Flash) | No | No | No |
| Streaming | Yes | Yes | Yes | Yes |
| Free tier | Yes | No | No | Yes (local) |

### Notes

- **Ollama**: Function calling support varies by model. Some models may not support tool use reliably. Vision capabilities depend on the specific model (e.g., `llava` supports images, `llama3.1` does not).
- **Anthropic**: Works well but does not have Gemini's smart routing. All tasks use the same model.
- **OpenAI**: Full feature parity with Gemini except for smart routing.

## Adding a New Provider

To add a new LLM provider:

1. **Create a client module** in `llm/`:

```python
# llm/my_provider_client.py
from llm.client import LLMClient
from llm.types import LLMResponse, LLMConfig, ContentPart

class MyProviderClient(LLMClient):
    async def generate(self, config: LLMConfig, messages: list[dict]) -> LLMResponse | None:
        # Implement API call
        ...

    def format_tool_result(self, tool_call_id: str, tool_name: str, result: dict) -> dict:
        # Format tool results for this provider's API format
        ...

    def format_user_message(self, text: str, image_bytes: bytes | None = None) -> dict:
        # Format user messages
        ...

    def format_assistant_message(self, parts: list[ContentPart]) -> dict:
        # Format assistant messages for conversation history
        ...
```

2. **Register in the factory** (`llm/client.py`):

```python
elif provider == "my_provider":
    from llm.my_provider_client import MyProviderClient
    _client_cache = MyProviderClient()
```

3. **Add environment variables** to `config.py`:

```python
MY_PROVIDER_API_KEY: str = os.environ.get("MY_PROVIDER_API_KEY", "")
MY_PROVIDER_MODEL: str = os.environ.get("MY_PROVIDER_MODEL", "default-model")
```

4. **Test** by setting `LLM_PROVIDER=my_provider` in `.env` and running `python cli.py`.

## Environment Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_PROVIDER` | No | `gemini` | Provider selection: `gemini`, `openai`, `anthropic`, `ollama` |
| `GEMINI_API_KEY` | If using Gemini | -- | Google AI Studio API key |
| `GEMINI_API_KEY_BACKUP` | No | -- | Fallback Gemini key for rate limit recovery |
| `OPENAI_API_KEY` | If using OpenAI | -- | OpenAI platform API key |
| `OPENAI_MODEL` | No | `gpt-4o` | OpenAI model name |
| `ANTHROPIC_API_KEY` | If using Anthropic | -- | Anthropic console API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-6` | Anthropic model name |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434/v1` | Ollama server URL |
| `OLLAMA_MODEL` | No | `llama3.1` | Ollama model name |
