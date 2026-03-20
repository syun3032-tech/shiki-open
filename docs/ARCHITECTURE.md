# Architecture

This document describes the internal architecture of Shiki.

## System Overview

```
                     +-------------------+
                     |   Entry Points    |
                     |  cli.py           |
                     |  main.py (LINE)   |
                     |  discord_bot.py   |
                     +--------+----------+
                              |
                              v
+------------------+  +------------------+  +------------------+
|  Security Gate   |->|   Agent Loop     |->|  LLM Abstraction |
|  (security/)     |  |  (agent/loop.py) |  |  (llm/)          |
+------------------+  +--------+---------+  +------------------+
                              |                      |
                     +--------+--------+             |
                     |                 |             |
              +------v------+  +------v------+      |
              | Tool System |  |   Memory    |      |
              | (tools/)    |  | (memory/)   |      |
              +------+------+  +------+------+      |
                     |                 |             |
              +------v------+  +------v------+      |
              |  Platform   |  |  .ritsu/    |      |
              |  Layer      |  |  SOUL.md    |      |
              | (platform_  |  |  MEMORY.md  |      |
              |  layer/)    |  |  episodes/  |      |
              +-------------+  +-------------+      |
                                                    |
                                             +------v------+
                                             | Providers:  |
                                             | - Gemini    |
                                             | - OpenAI    |
                                             | - Anthropic |
                                             | - Ollama    |
                                             +-------------+
```

## ReAct Loop

The agent loop (`agent/loop.py`) is the heart of Shiki. It implements the ReAct (Reasoning + Acting) pattern:

```
User Message
    |
    v
[Build Context]  <-- SOUL.md + MEMORY.md + Episodes + Playbooks
    |
    v
[Send to LLM with Tools]
    |
    v
[LLM Response] ---> Text only? --> Return to user (loop ends)
    |
    Tool call?
    |
    v
[Security Gate Check]  <-- Validate level, path, permissions
    |
    v
[Execute Tool]
    |
    v
[Feed result back to LLM]
    |
    v
[Loop continues until text response or MAX_ITERATIONS]
```

Key behaviors:
- **MAX_ITERATIONS = 50** -- prevents infinite loops
- **Smart routing** -- simple tasks use Flash (fast/cheap), complex tasks use Pro (capable)
- **Failure classification** -- transient errors retry, permanent errors abort, permission errors escalate
- **WAL (Write-Ahead Log)** -- crash recovery for multi-step operations
- **Correction detection** -- if the user corrects Shiki, the correction is extracted and stored

## Tool System (6 Layers)

Tools are organized in layers of increasing abstraction:

### Layer 1: Platform-Native (osascript / PowerShell / xdotool)

Direct OS-level commands. Fastest and most reliable for supported operations.

- `open_app`, `open_url`, `set_volume`, `toggle_dark_mode`, `show_notification`
- macOS: uses osascript (AppleScript/JXA)
- Windows: uses PowerShell
- Linux: uses xdotool and shell commands

### Layer 2: Playwright (Browser)

Headless or headed browser automation with stealth capabilities.

- `browser_navigate`, `browser_click`, `browser_type`, `browser_extract`
- Anti-bot evasion via playwright-stealth
- Multi-profile Chrome support

### Layer 3: GUI Automation (Mouse/Keyboard)

Screen-coordinate-based interaction for apps without APIs.

- `click`, `double_click`, `right_click`, `drag`, `scroll`
- `type_text`, `press_key`
- `take_screenshot`, `crop_screenshot`

### Layer 4: CodeAct (Python Sandbox)

Execute Python code in a sandboxed environment for data analysis, calculations, and automation scripts.

- `execute_code` -- runs Python with restricted imports
- Output capture (stdout, stderr, return value)
- Timeout enforcement

### Layer 5: Claude Code Delegation

Delegates complex software engineering tasks to Claude Code.

- `claude_code` -- sends task description, receives completed code
- Used for multi-file refactoring, test writing, architecture changes

### Layer 6: MCP (External Services)

Model Context Protocol integration for third-party services.

- Dynamic tool discovery from MCP servers
- Configured via `mcp_ext/mcp_servers.json`
- Supports Notion, GitHub, Google Calendar, and 1200+ services

### Tool Effect Gate

Every tool execution passes through the Security Gate (`security/gate.py`):

| Level | Approval | Examples |
|-------|----------|----------|
| READ | Automatic | `take_screenshot`, `read_file`, `get_running_apps` |
| WRITE | Path-validated automatic | `type_text`, `click`, `write_file` |
| ELEVATED | Notification sent | `open_app`, `run_command`, `move_file` |
| DESTRUCTIVE | Explicit approval required | `delete_file`, system-level operations |

## Memory System

Shiki's memory is file-based (Markdown + JSON), making it human-readable and debuggable.

### SOUL.md (Personality)

Defines Shiki's character, speech patterns, and behavioral guidelines. Generated by the setup wizard or manually edited.

Location: `.ritsu/SOUL.md`

### MEMORY.md (Long-term Memory)

Accumulated knowledge about the owner: preferences, work patterns, important facts.

Location: `.ritsu/MEMORY.md`

### Session Summaries

Each conversation session is summarized and stored. Used for continuity across restarts.

Location: `.ritsu/sessions/YYYY-MM-DD-NNN.md`

### Daily Summaries

End-of-day digests combining all session summaries.

Location: `.ritsu/daily/YYYY-MM-DD.md`

### Topic Memory

Extracted topics and knowledge organized by subject.

Location: `.ritsu/topics/`

### Episodic Memory

Records of specific interactions (successes and failures) that can be recalled when similar situations arise. Enables few-shot learning from past experience.

Module: `agent/episodic_memory.py`

### Playbooks

Learned multi-step procedures. When Shiki successfully completes a complex task, the steps are recorded as a playbook. On similar future requests, the playbook is injected as a few-shot example.

Module: `agent/playbook.py`

## Multi-Model Abstraction

The `llm/` module provides a unified interface across providers:

```
llm/
├── client.py            # Abstract LLMClient base class + get_client() factory
├── types.py             # LLMResponse, LLMConfig, ToolDefinition, ContentPart
├── gemini.py            # Google Gemini (default)
├── openai_client.py     # OpenAI (GPT-4o, etc.)
├── anthropic_client.py  # Anthropic Claude
└── ollama_client.py     # Ollama (local models)
```

All providers implement the same interface:
- `generate(config, messages)` -- send messages with tool definitions, receive response
- `format_tool_result()` -- format tool outputs for the next turn
- `format_user_message()` -- format text and optional images
- `format_assistant_message()` -- format assistant responses for history

### Smart Routing

The router (`agent/router.py`) analyzes task complexity using pattern matching:

- **Simple tasks** (greetings, screenshots, volume changes) -> Flash model (fast, cheap)
- **Complex tasks** (multi-step operations, code writing, analysis) -> Pro model (capable)

This applies primarily to Gemini (Pro vs Flash), but the routing logic can inform model selection for other providers.

## MCP Integration

Shiki supports the Model Context Protocol for connecting to external services:

```
mcp_ext/
├── client.py           # MCP client (session management, tool discovery)
├── bridge.py           # Bridge between Shiki tools and MCP tools
├── mcp_servers.json    # Server configuration
└── servers/            # Custom MCP server implementations
```

Configuration is file-based (`mcp_servers.json`). Adding a new MCP server requires only a JSON entry -- no code changes.

## Scheduler

The scheduler (`agent/scheduler.py`) enables proactive behavior:

- **Morning briefing** -- generates a summary of the day's schedule
- **Reminders** -- user-defined time-based notifications
- **Periodic checks** -- hooks for future MCP-based monitoring

Built on asyncio (no external scheduler dependency). Includes rate-limit detection to prevent API abuse.

## Platform Layer

The platform layer (`platform_layer/`) abstracts OS-specific operations:

```
platform_layer/
├── base.py       # Abstract interface
├── macos.py      # macOS: Quartz, osascript, Cocoa APIs
├── windows.py    # Windows: pyautogui, PowerShell, Win32
└── linux.py      # Linux: xdotool, scrot, xclip
```

The correct implementation is selected at startup based on `sys.platform`. Tool code calls the platform layer instead of OS-specific APIs directly.

## Continuous Observer

The observer (`agent/continuous_observer.py`) optionally monitors the user's screen at intervals:

1. Takes periodic screenshots
2. Analyzes work patterns (what apps are used, what tasks are common)
3. Detects opportunities to help
4. Evolves skills based on observed patterns

This feature is opt-in and controlled via `user_config.json`.

## Context Injection

The context engine (`agent/context.py`) builds the system prompt for each LLM call by assembling:

1. SOUL.md (personality and guidelines)
2. MEMORY.md (long-term knowledge)
3. Recent daily summaries
4. Relevant episodic memories (similarity-matched)
5. Matching playbooks (if the current task resembles a past one)
6. Current scratchpad / plan state
7. Available tool definitions
