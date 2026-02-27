# Contributing to Ignis

Thank you for your interest in contributing. Please read this guide before submitting changes.

## Development Setup

**Requirements**: Python 3.11+, Docker, Docker Compose

```bash
git clone https://github.com/73nuts/crypto-signal-bot.git
cd crypto-signal-bot

# Install dependencies (including dev tools)
pip install -e ".[dev]"

# Copy environment config
cp .env.example .env
cp .env.telegram.example .env.telegram
# Edit .env and .env.telegram with your API keys and credentials
```

**Start local dependencies:**

```bash
docker-compose up -d mysql redis
```

**Verify setup:**

```bash
python -m src.strategies.swing.scheduler --status
```

## Make Targets

| Target | Description |
|---|---|
| `make setup` | Install dependencies (`pip install -e ".[dev]"`) |
| `make test` | Run all tests (requires MySQL) |
| `make test-unit` | Run unit tests only (no external dependencies) |
| `make lint` | Run ruff linter |
| `make format` | Run ruff formatter |

## Code Style

- **Formatter/Linter**: [ruff](https://docs.astral.sh/ruff/)
- **Type hints**: required for all function signatures
- **Docstrings**: [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)

> Note: ruff runs automatically on file save if you use Claude Code (via a Claude Code hook). External contributors should run `make lint` and `make format` manually before submitting a PR.

Run checks manually:

```bash
make lint
make format
```

## Branching

- `main` — stable, production-ready
- `auto-trading` — active development branch
- Feature branches: `feat/<short-description>`
- Bug fixes: `fix/<short-description>`

## Commit Format

```
<type>: <description>
```

Types: `feat`, `fix`, `refactor`, `docs`

Example: `fix: handle empty orderbook response from Binance`

## Pull Request Process

1. Fork the repository
2. Create a branch from `auto-trading`
3. Make your changes with appropriate tests
4. Ensure `ruff check .` passes with no errors
5. Submit a PR against `auto-trading`
6. Describe what changed and why in the PR body

## Reporting Issues

**Bug reports** must include:
- Steps to reproduce
- Relevant log output (from `docker-compose logs`)
- Python version and OS

**Feature requests** must include:
- Use case: what problem does this solve?
- Proposed approach (optional)

## Architecture Notes

Before making significant changes, read:

- `docs/development/ARCHITECTURE.md` — system design
- `docs/analysis/SWING_STRATEGY.md` — strategy definition

Changes to data pipelines, DB schema, or trading logic require extra review.
