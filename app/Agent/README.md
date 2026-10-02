# Agent

ADK (Google Agent Development Kit) agent for the CLIR hackathon. The model is
served by Ollama and reached through LiteLLM, so the backend is configuration,
not code: see `LLM_MODEL` and `LLM_API_BASE` in `.env.example`.

## Prerequisites

- [Ollama](https://ollama.com) running locally, with the configured model pulled:

```bash
ollama pull llama3.1
```

## Setup

```bash
cp .env.example .env
uv sync
```

`uv sync` creates `.venv`, installs runtime and dev dependencies, and installs
`src/Agent` in editable mode so imports resolve without path manipulation.

## Commands

```bash
uv run Agent                      # print the resolved agent configuration
uv run adk run src/Agent/agent    # chat with the agent in the terminal
uv run chat                       # in-memory chat loop; type "chao pescao" to exit
uv run adk web src/Agent          # browser dev UI, pick "agent"
uv run pytest          # run tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run pyright         # type check
```

## Dependencies

```bash
uv add <package>              # runtime dependency
uv add --dev <package>        # dev-only dependency
uv lock --upgrade-package <package>
uv sync                       # reconcile .venv with uv.lock
```

Commit `uv.lock`. It is what makes installs reproducible across machines and
CI, which is why `pyproject.toml` declares `>=` floors instead of exact pins.
