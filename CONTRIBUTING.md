# Contributing to Shiki

Thank you for your interest in contributing to Shiki. This guide explains how to get involved.

## Reporting Bugs

Open a [GitHub Issue](https://github.com/MatsuShun0686/shiki/issues) with:

- A clear title describing the problem
- Steps to reproduce
- Expected behavior vs. actual behavior
- Your environment: OS, Python version, LLM provider
- Relevant log output (check `logs/` directory)

## Suggesting Features

Open a GitHub Issue with the "feature request" label. Include:

- What problem the feature solves
- Proposed solution or approach
- Whether you are willing to implement it

## Development Setup

```bash
# Clone the repository
git clone https://github.com/MatsuShun0686/shiki.git
cd shiki

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run the setup wizard
python setup_wizard.py

# Verify everything works
python cli.py
```

## Code Style

- **Python 3.12+** -- use modern syntax (type hints, `match`, `|` union types)
- **Type hints** -- all function signatures should include type annotations
- **Docstrings** -- use triple-quoted docstrings for modules, classes, and public functions
- **Naming** -- `snake_case` for functions and variables, `PascalCase` for classes
- **Imports** -- standard library first, then third-party, then local (separated by blank lines)
- **Line length** -- 100 characters preferred, 120 max
- **Logging** -- use `logging.getLogger("shiki.module_name")`, not `print()`

### Example

```python
"""Module description -- one line summary.

Extended description if needed.
"""

import logging
from pathlib import Path

from llm.types import LLMResponse

logger = logging.getLogger("shiki.my_module")


def process_input(text: str, max_length: int = 1000) -> str | None:
    """Process user input and return cleaned text.

    Args:
        text: Raw input text.
        max_length: Maximum allowed length.

    Returns:
        Cleaned text, or None if input is invalid.
    """
    if not text or len(text) > max_length:
        return None
    return text.strip()
```

## Adding a New Tool

1. Create a Python file in `tools/` with your tool function(s)
2. Register the tool in `agent/tools_config.py`:
   - Add the function to `TOOL_FUNCTIONS`
   - Add the Gemini function declaration to `GEMINI_TOOLS`
3. Assign a security level in `security/gate.py` (`TOOL_LEVELS`)
4. Test via CLI: `python cli.py`

## Pull Request Process

1. **Fork** the repository and create a feature branch from `main`
2. **Make your changes** with clear, focused commits
3. **Test** your changes locally across at least one platform
4. **Open a PR** with:
   - A clear title (under 70 characters)
   - Description of what changed and why
   - Any relevant issue numbers
5. **Respond to review feedback** promptly

### PR Guidelines

- Keep PRs focused -- one feature or fix per PR
- Do not include unrelated formatting changes
- Update documentation if you change user-facing behavior
- Add yourself to the contributors list if this is your first PR

## Security Considerations

If you discover a security vulnerability, please report it privately via email rather than opening a public issue. See the security policy for details.

When contributing code that interacts with the filesystem, network, or external APIs:
- Always use the Security Gate (`security/gate.py`) for tool execution
- Validate file paths through `security/path_validator.py`
- Never log or expose API keys
- Follow the existing patterns in `security/env_filter.py`

## Code of Conduct

Be respectful and constructive in all interactions. We follow the [Contributor Covenant](https://www.contributor-covenant.org/) code of conduct.

## Questions?

Open a GitHub Discussion or reach out via Issues. We are happy to help you get started.
