# Quick Start Guide

This guide walks you through setting up Shiki from scratch on any platform.

## Prerequisites

- **Python 3.12+** ([python.org](https://python.org))
- **Git** ([git-scm.com](https://git-scm.com))
- An API key for at least one LLM provider (Gemini recommended for free tier)

## Step 1: Clone and Install

```bash
git clone https://github.com/MatsuShun0686/shiki.git
cd shiki
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Step 2: Platform-Specific Dependencies

### macOS

All macOS dependencies (pyobjc-framework-Quartz) are included in requirements.txt and install automatically.

You must grant permissions in **System Settings > Privacy & Security**:
- **Screen Recording** -- required for screenshots
- **Accessibility** -- required for mouse/keyboard control

### Windows

```bash
pip install pyautogui Pillow pyperclip
```

No additional system permissions are typically needed.

### Linux (Ubuntu/Debian)

```bash
pip install pyautogui Pillow pyperclip
sudo apt install xdotool scrot xclip libnotify-bin
```

For other distributions, install equivalent packages for screenshot capture (scrot), clipboard (xclip), and desktop automation (xdotool).

## Step 3: Install Playwright Browser

```bash
playwright install chromium
```

This downloads the Chromium binary used for browser automation.

## Step 4: Configure API Keys

Create a `.env` file in the project root:

```bash
# Required: at least one LLM provider
GEMINI_API_KEY=your_key_here

# Optional: choose a different provider
# LLM_PROVIDER=openai
# OPENAI_API_KEY=sk-...

# Optional: LINE Bot
# LINE_CHANNEL_SECRET=...
# LINE_CHANNEL_ACCESS_TOKEN=...
# OWNER_LINE_USER_ID=...

# Optional: Discord Bot
# DISCORD_BOT_TOKEN=...
# DISCORD_OWNER_ID=...
```

### Getting API Keys

| Provider | Where to Get | Free Tier |
|----------|-------------|-----------|
| Gemini | [Google AI Studio](https://aistudio.google.com/) | Yes (generous) |
| OpenAI | [platform.openai.com](https://platform.openai.com/api-keys) | No |
| Anthropic | [console.anthropic.com](https://console.anthropic.com/) | No |
| Ollama | Local install at [ollama.com](https://ollama.com/) | Free (runs locally) |

## Step 5: First Run

```bash
python cli.py
```

On first launch, the **setup wizard** starts automatically and asks you to configure:

1. **Your name** -- how Shiki addresses you
2. **Shiki's personality** -- friendly, cool, energetic, tsundere, or custom
3. **Communication channels** -- CLI, LINE, Discord
4. **Browser profiles** -- map Chrome profiles to email accounts
5. **Observation mode** -- whether Shiki watches your screen to learn patterns

The wizard creates `user_config.json` with your settings and `.ritsu/SOUL.md` with the personality definition.

## Step 6: Start Using Shiki

### CLI Mode (Terminal)

```bash
python cli.py
```

Type naturally. Examples:

```
> Open Chrome and go to github.com
> Take a screenshot
> What's on my screen right now?
> Create a Python script that calculates fibonacci numbers
> Remind me to check email in 30 minutes
```

### LINE Bot Mode

```bash
python main.py
```

Starts a FastAPI server on port 8000. Configure your LINE webhook URL to point to `https://your-domain/callback`.

### Discord Bot Mode

```bash
python cli.py discord
```

Or run the Discord bot directly:

```bash
python discord_bot.py
```

## Troubleshooting

### "GEMINI_API_KEY is not set"

Make sure your `.env` file is in the project root directory (same folder as `cli.py`).

### Screenshot permission denied (macOS)

Go to **System Settings > Privacy & Security > Screen Recording** and add your terminal app (Terminal.app, iTerm2, etc.).

### Playwright browser not found

Run `playwright install chromium` again. If it fails, try:

```bash
python -m playwright install chromium
```

### Mouse/keyboard not working (macOS)

Grant **Accessibility** permission in **System Settings > Privacy & Security > Accessibility** for your terminal app.

### Import errors on Windows/Linux

Make sure you installed the platform-specific packages:

```bash
pip install pyautogui Pillow pyperclip
```

### Port 8000 already in use

Change the port in your `.env`:

```bash
PORT=8080
```

### Rate limit errors

Shiki has built-in rate limiting (60 messages/minute). If you hit API provider rate limits, wait a moment or switch to a different provider.

## Next Steps

- Read the [Architecture Guide](ARCHITECTURE.md) to understand how Shiki works
- See [Multi-Model Setup](MULTI_MODEL.md) for using different LLM providers
- Check [Docker Guide](DOCKER.md) for containerized deployment
- Add MCP integrations by editing `mcp_ext/mcp_servers.json`
