# Shiki (識) -- Self-Identifying Environment-Integrated Control Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://python.org)

**An open-source AI agent that lives on your PC, acting as a personal assistant that can see your screen, control your apps, browse the web, remember context, and learn from your habits.**

<!-- ![Demo](docs/demo.gif) -->

Shiki is a ReAct-based AI agent with 36+ built-in tools spanning desktop automation, browser control, file management, code execution, and more. It supports multiple LLM backends (Gemini, OpenAI, Claude, Ollama), runs on Mac/Windows/Linux, and communicates via CLI, LINE, or Discord.

---

## Features

### Desktop Automation
- Screenshot capture and screen recognition (vision)
- Mouse control (click, drag, scroll) and keyboard input
- App launching, window management, volume control
- Cross-platform: macOS (Quartz/osascript), Windows (pyautogui/PowerShell), Linux (xdotool)

### Browser Control
- Playwright-based headless and headed browser automation
- Stealth mode for anti-bot evasion
- Multi-profile Chrome support (personal/work accounts)
- URL navigation with profile-aware routing

### Code Execution
- Sandboxed Python execution (CodeAct)
- Terminal command execution with safety gates
- Claude Code delegation for complex development tasks

### Memory and Learning
- Persistent personality (SOUL.md) and long-term memory (MEMORY.md)
- Session summaries, daily digests, and topic extraction
- Episodic memory for recalling past interactions
- Playbook system for learned multi-step procedures
- Continuous observation mode that detects work patterns

### Proactive Behavior
- Morning briefing with daily schedule
- Reminder system
- Scheduled autonomous tasks
- Standing orders (recurring instructions)

### Integrations
- LINE Bot, Discord Bot, and terminal CLI channels
- MCP (Model Context Protocol) for Notion, GitHub, Google Calendar, and 1200+ services
- Extensible tool system -- add new tools with a single Python file

### Security
- 5-layer security architecture (see [Security](#security))
- Tool Effect Gate with 4 approval levels
- Path validation, environment variable filtering, output scanning

---

## Quick Start

Get running in 5 minutes:

```bash
# 1. Clone
git clone https://github.com/syun3032-tech/shiki-open.git
cd shiki

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browser
playwright install chromium

# 5. Set your API key
echo "GEMINI_API_KEY=your_key_here" > .env

# 6. Run (setup wizard launches automatically on first run)
python cli.py
```

The setup wizard will guide you through configuring your name, Shiki's personality, communication channels, and browser profiles.

For detailed platform-specific instructions, see [docs/QUICKSTART.md](docs/QUICKSTART.md).

---

## Multi-Model Support

Shiki supports multiple LLM providers. Set `LLM_PROVIDER` in your `.env` file:

| Provider | Env Vars | Default Model |
|----------|----------|---------------|
| Gemini (default) | `GEMINI_API_KEY` | `gemini-2.5-pro` |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | `gpt-4o` |
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | `claude-sonnet-4-6` |
| Ollama (local) | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | `llama3.1` |

```bash
# .env example for OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
```

Smart routing automatically selects between Pro (complex tasks) and Flash (simple tasks) models to optimize cost and speed.

For the full guide, see [docs/MULTI_MODEL.md](docs/MULTI_MODEL.md).

---

## Docker

```bash
# 1. Configure environment
cp .env.example .env   # edit with your API keys

# 2. Run
docker compose up -d

# 3. Check health
curl http://localhost:8000/health
```

The Docker image runs the LINE/Discord webhook server (FastAPI on port 8000). Desktop automation tools are unavailable inside the container. For full functionality, run natively.

See [docs/DOCKER.md](docs/DOCKER.md) for details.

---

## Architecture

```
User Input (CLI / LINE / Discord)
        |
        v
  +------------------+
  |   Agent Loop     |  ReAct: Observe -> Think -> Act -> Repeat
  |   (loop.py)      |
  +------------------+
        |
   +----+----+
   |         |
   v         v
+-------+ +----------+
| Smart | | Context  |  SOUL.md + MEMORY.md + Episodes + Playbooks
| Router| | Engine   |
+-------+ +----------+
   |
   v
+-------------------+
|  LLM Abstraction  |  Gemini / OpenAI / Claude / Ollama
|  (llm/)           |
+-------------------+
   |
   v
+-------------------+
|  Tool Execution   |  36+ tools across 6 layers
|  (tools/)         |
+-------------------+
   |
   +-- Layer 1: osascript / platform-native
   +-- Layer 2: Playwright (browser)
   +-- Layer 3: GUI automation (mouse/keyboard)
   +-- Layer 4: CodeAct (Python sandbox)
   +-- Layer 5: Claude Code delegation
   +-- Layer 6: MCP (external services)
   |
   v
+-------------------+
|  Security Gate    |  READ -> WRITE -> ELEVATED -> DESTRUCTIVE
|  (security/)      |
+-------------------+
```

For the full architecture document, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Security

Shiki is designed with security-first principles:

| Layer | Mechanism | Description |
|-------|-----------|-------------|
| 1 | Authentication | Owner-only access with timing-safe comparison |
| 2 | Tool Effect Gate | 4-tier approval: READ (auto) / WRITE (path-validated) / ELEVATED (notified) / DESTRUCTIVE (requires approval) |
| 3 | Path Validation | Filesystem access restricted to Desktop, Documents, Downloads |
| 4 | Env Var Filter | API keys never exposed to the AI model |
| 5 | Output Scanning | Automatic credential leak detection |
| 6 | Anomaly Detection | OWASP-based real-time monitoring |
| 7 | Rate Limiting | 60 messages/minute cap |

---

## Configuration

### Environment Variables (.env)

```bash
# Required
GEMINI_API_KEY=your_gemini_key

# LLM Provider (optional, default: gemini)
LLM_PROVIDER=gemini

# LINE Bot (optional)
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
OWNER_LINE_USER_ID=...

# Discord Bot (optional)
DISCORD_BOT_TOKEN=...
DISCORD_OWNER_ID=...

# Integrations (optional)
NOTION_API_KEY=...
GOOGLE_CALENDAR_ID=primary
```

Get a free Gemini API key at [Google AI Studio](https://aistudio.google.com/).

### User Settings (user_config.json)

Generated by the setup wizard on first run. You can also copy and edit the example:

```bash
cp user_config.example.json user_config.json
```

Key settings: owner name, Shiki's personality, allowed apps, browser profiles, observation mode. See [user_config.example.json](user_config.example.json) for the full schema.

To re-run the setup wizard:

```bash
python setup_wizard.py
```

---

## Project Structure

```
shiki/
├── main.py                  # FastAPI + LINE Webhook server
├── cli.py                   # Terminal interactive mode
├── discord_bot.py           # Discord Bot
├── config.py                # Environment variable management
├── user_config.py           # User settings manager
├── setup_wizard.py          # First-run setup wizard
├── agent/
│   ├── loop.py              # ReAct loop (core)
│   ├── context.py           # Context injection engine
│   ├── router.py            # Smart model routing
│   ├── scheduler.py         # Proactive behavior scheduler
│   ├── episodic_memory.py   # Episode recall
│   ├── playbook.py          # Learned procedures
│   ├── skills.py            # Built-in skills
│   └── tools_config.py      # Tool definitions and schemas
├── tools/
│   ├── screenshot.py        # Screen capture (cross-platform)
│   ├── desktop.py           # Desktop automation
│   ├── mouse.py             # Mouse control
│   ├── browser.py           # Playwright browser
│   ├── terminal.py          # Command execution
│   ├── code_executor.py     # CodeAct (Python sandbox)
│   ├── claude_code.py       # Claude Code delegation
│   └── filesystem.py        # File operations
├── llm/                     # Multi-provider LLM abstraction
│   ├── client.py            # Abstract base + factory
│   ├── gemini.py            # Google Gemini
│   ├── openai_client.py     # OpenAI
│   ├── anthropic_client.py  # Anthropic Claude
│   └── ollama_client.py     # Ollama (local)
├── security/                # 5-layer security
│   ├── gate.py              # Tool Effect Gate
│   ├── path_validator.py    # Filesystem access control
│   ├── env_filter.py        # API key protection
│   ├── output_validator.py  # Credential leak detection
│   └── anomaly_detector.py  # OWASP-based monitoring
├── memory/                  # Memory system
├── mcp_ext/                 # MCP integration
├── platform_layer/          # OS abstraction (macOS/Windows/Linux)
├── .ritsu/                  # Personality and memory data (gitignored)
├── .env                     # API keys (gitignored)
└── user_config.json         # User settings (gitignored)
```

---

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Reporting bugs and suggesting features
- Development setup
- Code style and PR process

---

## License

MIT License. See [LICENSE](LICENSE) for details.

Copyright (c) 2026 Shunsuke Matsumoto

---

## Acknowledgments

Inspired by:

- [OpenHands](https://github.com/All-Hands-AI/OpenHands) -- AI software development agent
- [Browser-Use](https://github.com/browser-use/browser-use) -- AI + Playwright
- [Claude Computer Use](https://docs.anthropic.com/) -- Vision + GUI automation
- [Manus AI](https://manus.im/) -- CodeAct, scratchpad patterns

---

## 日本語

識（しき）は、あなたのPCに常駐して秘書として働くオープンソースAIエージェントです。画面認識、アプリ操作、Web検索、コード実行、記憶・学習機能を備え、Mac/Windows/Linuxで動作します。

主な特徴:
- 36以上のツール（スクショ、マウス、キーボード、ブラウザ、ファイル、ターミナル、コード実行）
- ReActループによる自律的タスク実行
- 記憶システム（性格、長期記憶、エピソード記憶、プレイブック）
- マルチLLM対応（Gemini / OpenAI / Claude / Ollama）
- 3チャネル対応（CLI / LINE Bot / Discord Bot）
- MCP統合（Notion、GitHub、Google Calendar等）
- 5層セキュリティ

詳細なセットアップ手順や技術ドキュメントは英語版をご参照ください。セットアップウィザードは日本語に対応しています。
