# Blender Open MCP — AGENTS.md

## Project Overview
**blender-open-mcp** is a local-first MCP (Model Context Protocol) server for
controlling a live Blender session, with a **provider-agnostic LLM backend**.
The same codebase talks to Ollama, LM Studio, llama.cpp server, OpenAI, Azure
AI Foundry, and any OpenAI-compatible endpoint. Providers can be set at
startup (CLI/env) and switched at runtime through MCP tools.

## Repository Layout
- `addon.py` — single-file Blender add-on. Registers operators/panel, runs a
  TCP server (default `localhost:9876`) that receives newline-delimited JSON
  commands (`{"type": ..., "params": {...}}`) and dispatches to handlers in
  `HANDLERS`. Handlers cover scene info, object create/modify/delete,
  materials, rendering, code execution, and PolyHaven download/set-texture.
- `src/blender_open_mcp/`
  - `server.py` — MCP server. Registers all `blender_*` tools with flat
    signatures, owns the Blender TCP bridge (`_send_blender_command`), the
    runtime LLM state (`_llm_state`), and the CLI (`main()`).
  - `llm.py` — provider layer: `PROVIDERS` registry + `PROVIDER_ALIASES`,
    `resolve_provider()`, `chat()`, `list_models()`, `ProviderError`. Add new
    backends here.
  - `client/` — canonical async MCP client (`BlenderMCPClient`) + CLI.
  - `prompts.py` — MCP Prompts (`blender_build_scene`, `blender_review_scene`,
    `blender_configure_llm`) registered via `register_prompts(mcp)`.
  - `__init__.py` — exports `main`.
- `client/` — compat wrapper so legacy `from client import …` imports work
  from source; it bootstraps `src/` onto `sys.path` and re-exports the
  canonical module.
- `tests/` — pytest suites: `test_server.py`, `test_client.py`,
  `test_addon.py` (bpy mocked).
- `pyproject.toml` — hatchling build, deps, `blender-mcp` /
  `blender-mcp-client` console scripts, pytest config.
- `.venv/` — local virtualenv used for development/testing (gitignored).

## Key Design Decisions
- **Flat tool signatures.** Every `@mcp.tool` function takes explicit keyword
  parameters (no `params: X` wrapper objects). FastMCP 4 advertises whatever
  the signature says, so standard MCP clients can call tools with top-level
  args. Never reintroduce single-wrapper-model signatures for tools.
- **Provider-agnostic routing.** `_query_llm()` in `server.py` fills missing
  provider fields from `_llm_state` and delegates to `llm.chat()`. Per-call
  overrides (`provider`/`base_url`/`model`/`api_key`) are supported on
  `blender_ai_prompt`.
- **Runtime provider switching.** `blender_set_llm_provider` mutates
  `_llm_state`; `blender_get_llm_provider` reports it (API key masked);
  `blender_list_llm_models` lists models. Ollama-only aliases
  (`blender_set_ollama_model`, `blender_set_ollama_url`,
  `blender_get_ollama_models`) are thin wrappers for backward compatibility.
- **Blender bridge protocol.** Server sends
  `{"type": "<handler>", "params": {...}}\n` over TCP and reads one JSON
  response line: `{"status":"ok","result":...}` or
  `{"status":"error","message":...}`.

## Conventions
- Python ≥ 3.10, type hints throughout, stdlib `typing` (`Dict`, `List`,
  `Optional`) — do not rely on newer 3.11+ syntax unless guarded.
- Imports for external MCP machinery: `from fastmcp import FastMCP` (the
  project runs fastmcp 4 + mcp 2; `mcp.server.fastmcp` no longer exists).
- Tool annotations are passed to `@mcp.tool(annotations={...})` and use the
  keys `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`,
  `title`.
- Provider specs go in `llm.py` `PROVIDERS` (style: `openai`, `ollama`,
  `azure`) plus an alias in `PROVIDER_ALIASES`; URL/payload/response
  normalization lives in `llm.py`, never in `server.py` tool bodies.
- Errors returned to agents are friendly strings starting with `Error: ...`
  (see `_handle_blender_error` / `_handle_provider_error`); exceptions are
  caught inside tools, not propagated.

## Safe Refactoring Rules
- Changing an MCP tool signature means updating:
  1. the `@mcp.tool` function in `server.py`,
  2. any typed wrapper in `src/blender_open_mcp/client/client.py`,
  3. tests that call it.
- Adding a Blender-side handler requires: new handler function in `addon.py`,
  registration in `addon.HANDLERS`, and a `blender_*` tool in `server.py` that
  calls `_send_blender_command("<handler_name>", ...)`.
- Do not expose secrets: `_redact_state()` masks the API key; keep that mask
  when adding state-reporting tools.
- Keep legacy aliases (`blender_set_ollama_*`) working unless intentionally
  deprecating them in a major release.

## Testing
- Run: `.venv/Scripts/python -m pytest tests -q` (Windows bash) or
  `python -m pytest tests -q`.
- Server tests mock `httpx` and exercise `_query_llm` for openai / ollama /
  lmstudio / llamacpp, provider state switching, tool registration/annotations,
  and PolyHaven pagination — all without Blender.
- Addon tests mock `bpy` at import time (see `tests/test_addon.py`) and test
  handlers/dispatch plus the timer-driven socket state machine.
- Client tests mock the MCP HTTP layer.
- `tests/test_integration.py` proves the wiring: an in-process `fastmcp.Client`
  against the server (tools + prompts), and the timer-driven `addon.py` TCP
  loop pumped by the test harness over a socket by the server bridge.
- Manual end-to-end: start the add-on in Blender, run
  `blender-mcp --transport http`, then
  `blender-mcp-client --host http://localhost:8000 tool blender_get_scene_info`.
- When adding an MCP prompt, mirror the pattern in `prompts.py` and register it
  there; update `tests/test_integration.py` expectations if the prompt set is
  part of a contract.
