# Docker Guide

Shiki provides Docker support for running the webhook server (LINE/Discord) in a containerized environment.

## Quick Start

```bash
# 1. Configure environment variables
cp .env.example .env
# Edit .env with your API keys

# 2. Build and run
docker compose up -d

# 3. Verify the server is running
curl http://localhost:8000/health
```

## Environment Variables

Pass API keys and configuration via the `.env` file. Docker Compose reads it automatically:

```bash
# Required
GEMINI_API_KEY=your_key_here

# LLM Provider (optional)
LLM_PROVIDER=gemini

# LINE Bot (required for LINE mode)
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
OWNER_LINE_USER_ID=...

# Discord Bot (required for Discord mode)
DISCORD_BOT_TOKEN=...
DISCORD_OWNER_ID=...
```

You can also pass variables directly:

```bash
docker compose run -e GEMINI_API_KEY=your_key shiki
```

## Volume Mounts

The `docker-compose.yml` mounts three directories:

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./.ritsu` | `/app/.ritsu` | Personality and memory data (persists across restarts) |
| `./user_config.json` | `/app/user_config.json` | User settings |
| `./logs` | `/app/logs` | Log files |

These mounts ensure your data persists when the container is recreated.

### Adding Custom Mounts

If you need additional directories (e.g., for file operations), add them to `docker-compose.yml`:

```yaml
services:
  shiki:
    volumes:
      - ./.ritsu:/app/.ritsu
      - ./user_config.json:/app/user_config.json
      - ./logs:/app/logs
      - ./my_data:/app/my_data  # additional mount
```

## Building a Custom Image

### Basic Build

```bash
docker build -t shiki .
```

### Build with Custom Tag

```bash
docker build -t shiki:v1.0 .
```

### Multi-Stage Build Details

The Dockerfile uses a two-stage build:

1. **Builder stage** -- installs Python dependencies into a prefix directory
2. **Runtime stage** -- copies only the installed packages and project files

This produces a smaller final image. The runtime stage includes:
- Playwright Chromium browser and its system dependencies
- Japanese font support (Noto CJK)
- A non-root `shiki` user for security

### Running Without Compose

```bash
docker run -d \
  --name shiki \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/.ritsu:/app/.ritsu \
  -v $(pwd)/user_config.json:/app/user_config.json \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  shiki
```

## Health Check

The container includes a built-in health check that pings `http://localhost:8000/health` every 30 seconds.

Check health status:

```bash
docker inspect --format='{{.State.Health.Status}}' shiki
```

Or via Docker Compose:

```bash
docker compose ps
```

## Limitations

### No Desktop Automation

The Docker container does not have access to a display server. The following features are unavailable:

- Screenshots (`take_screenshot`)
- Mouse and keyboard control (`click`, `type_text`, `press_key`)
- App launching (`open_app`)
- GUI observation mode

These tools will return errors if called inside the container.

### Available Features in Docker

The following features work fully inside the container:

- LINE and Discord bot communication
- Browser automation (Playwright with headless Chromium)
- Code execution (CodeAct)
- File operations (within mounted volumes)
- Memory system
- MCP integrations
- LLM interactions

### Workaround: Hybrid Setup

For full functionality, run the CLI natively on your desktop machine for desktop automation, and use Docker for the webhook server:

```bash
# Terminal 1: Native CLI for desktop features
python cli.py

# Terminal 2: Docker for webhook server
docker compose up -d
```

## Logs

View container logs:

```bash
# Follow logs
docker compose logs -f

# View last 100 lines
docker compose logs --tail 100
```

Application logs are also written to the `./logs` directory (mounted volume).

## Updating

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose up -d --build
```

## Stopping

```bash
# Stop the container
docker compose down

# Stop and remove volumes (caution: deletes persisted data)
docker compose down -v
```
