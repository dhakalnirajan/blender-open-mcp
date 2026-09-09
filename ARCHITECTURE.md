# Blender Open MCP — Architecture

## Purpose
Connect an external MCP client to a live Blender session and give the agent a
choice of local/remote LLM backends (Ollama, LM Studio, llama.cpp, OpenAI,
Azure, any OpenAI-compatible endpoint). The Blender add-on runs a small TCP
server inside Blender; the standalone FastMCP server wraps those commands as
MCP tools and routes AI prompts through a provider-agnostic chat layer.

## High-level components

```text
[External MCP client / agent]
        │  MCP (tools/list, tools/call)
        ▼
[FastMCP server]  src/blender_open_mcp/server.py
   │  - registers all blender_* tools (flat signatures)
   │  - owns _llm_state (active provider config) + _redact_state()
   │  - CLI entry (main): --transport, --blender-*, --llm-*
   ├───────────────────────────────┐
   ▼                               ▼
[Blender add-on bridge]      [LLM provider layer]  src/blender_open_mcp/llm.py
_send_blender_command()      PROVIDERS registry + chat()/list_models()
   │  TCP newline JSON              │
   ▼                                ▼
[addon.py TCP server]        [HTTP provider endpoints]
   │  HANDLERS dispatch             OpenAI-compatible /chat/completions
   ▼                                Ollama /api/chat, Azure deployment URL
[Blender bpy execution]
```

## Component details

### Blender add-on (`addon.py`)
- `bl_info` registers the add-on; `register()`/`unregister()` wire the Blender
  classes: `BlenderMCPProperties` (host/port on the Scene),
  `BLENDER_MCP_OT_StartServer` / `BLENDER_MCP_OT_StopServer`, and the
  `BLENDER_MCP_PT_Panel` in the 3D View sidebar.
- `_server_loop()` binds a TCP socket (default `localhost:9876`) and accepts
  connections in daemon threads.
- `_handle_client()` reads until `\n`, parses JSON, and calls `_dispatch()`.
- `_dispatch()` looks up `HANDLERS[type]`, runs the handler **on Blender's main
  thread** (see below), and returns `_ok(result)` / `_err(message)` as
  newline-terminated JSON.
- **Thread model.** Connections are accepted on daemon threads, but `bpy` is not
  thread safe: `bpy.ops.*` needs a window/view-layer context that only exists on
  the main thread, and a worker thread instead sees a restricted context
  (`'Context' object has no attribute 'active_object'`). So handlers are
  marshalled: `_run_on_main_thread()` queues a `_MainThreadJob`, the
  `_main_thread_pump()` `bpy.app.timers` callback drains the queue on the main
  thread, and the worker blocks on a `threading.Event` until the result (or
  exception) returns. The pump is registered by `BLENDER_MCP_OT_StartServer` and
  torn down by `BLENDER_MCP_OT_StopServer` / `unregister()`, which also releases
  any jobs still waiting.
- Commands in `WORKER_THREAD_COMMANDS` (PolyHaven lookups, LLM passthrough
  stubs) touch no `bpy` and deliberately stay on the worker thread so blocking
  network I/O never freezes Blender's UI. `download_polyhaven_asset` is in that
  set and marshals only its `bpy` import step.
- If no live pump is detected (bpy mocked in unit tests, or `_server_loop`
  started directly), `_run_on_main_thread()` falls back to running inline rather
  than deadlocking.
- `HANDLERS` currently includes: get_scene_info, get_object_info,
  create_object, modify_object, delete_object, set_material, render_image,
  execute_blender_code, get_polyhaven_categories, search_polyhaven_assets,
  download_polyhaven_asset, set_texture, plus passthrough stubs
  (set/get_llm_provider, get_ollama_models) that acknowledge the LLM config —
  the actual LLM state lives in the MCP server.

### MCP server (`src/blender_open_mcp/server.py`)
- FastMCP instance named `blender_open_mcp`.
- `_send_blender_command()` frames responses on the protocol's newline
  terminator rather than waiting for EOF, so a crash mid-reply is distinguished
  from a clean close; `ConnectionResetError` / `BrokenPipeError` surface as an
  actionable "Blender reset the connection" message instead of a raw traceback.
- **Blender tools** forward to the add-on via `_send_blender_command()`:
  - scene: `blender_get_scene_info`, `blender_get_object_info`
  - objects: `blender_create_object`, `blender_modify_object`,
    `blender_delete_object`
  - materials/render: `blender_set_material`, `blender_render_image`
  - code: `blender_execute_code`
- **PolyHaven tools** call `api.polyhaven.com` directly:
  `blender_get_polyhaven_categories`, `blender_search_polyhaven_assets`,
  `blender_download_polyhaven_asset`, `blender_set_texture`.
- **LLM/provider tools:**
  - `blender_ai_prompt(prompt, system_prompt, provider, base_url, api_key,
    model)` → `_query_llm(...)`.
  - `blender_get_llm_provider()` → masked config JSON.
  - `blender_set_llm_provider(provider, base_url, api_key, model, extra)` →
    mutates `_llm_state` through `_apply_provider_config()`.
  - `blender_list_llm_models(provider, base_url, api_key)` → model list.
  - Legacy Ollama aliases (`blender_set_ollama_model`, `blender_set_ollama_url`,
    `blender_get_ollama_models`) wrap the same state/tools.
- **MCP Prompts** (`src/blender_open_mcp/prompts.py`) register templates via
  `prompts/list` / `prompts/get`: `blender_build_scene`,
  `blender_review_scene`, `blender_configure_llm`. `register_prompts(mcp)` is
  called at import time in `server.py`.
- `_llm_state` holds `provider`, `base_url`, `model`, `api_key`, `extra`;
  defaults come from env vars (`BLENDER_OPEN_MCP_*`) or CLI args.
- All tools return strings: pretty JSON for success, or an `Error: ...` string
  for failures. Blender errors go through `_handle_blender_error()`; provider
  errors through `_handle_provider_error()`.

### Provider layer (`src/blender_open_mcp/llm.py`)
- `PROVIDERS`: spec per provider (`style`, `default_url`, `default_model`,
  `auth`, `label`) for `openai`, `openai_compat`, `lmstudio`, `llamacpp`,
  `ollama`, `azure`.
- `PROVIDER_ALIASES`: canonicalize names (`lm-studio` → `lmstudio`,
  `llama.cpp` → `llamacpp`, `vllm`/`groq`/`openrouter` → `openai_compat`, …).
- `resolve_provider()` validates/normalizes and raises `ProviderError`.
- `chat(messages, *, provider, base_url, api_key, model, temperature,
  max_tokens, extra)`:
  - `_chat_url()` builds the endpoint:
    - OpenAI style: `<base>/chat/completions`
    - Ollama: `<base>/api/chat` (or `<base>/v1/chat/completions` when base
      ends in `/v1`)
    - Azure: `https://<resource>.openai.azure.com/openai/deployments/
      <deployment>/chat/completions?api-version=<v>`
  - `_chat_payload()` builds the body; `_headers()` adds `Authorization:
    Bearer` or `api-key` per style; `extra` passes through provider-specific
    keys.
  - `_extract_content()` normalizes OpenAI-style (`choices[0].message.content`)
    and Ollama-native (`message.content`, plus `/api/generate` `response`)
    reply shapes.
- `list_models()`: Ollama `/api/tags`; other OpenAI-style providers
  `/models`; Azure raises a guidance `ProviderError`.

### Client (`src/blender_open_mcp/client/client.py`)
- `BlenderMCPClient` performs MCP initialize handshake over streamable HTTP,
  tracks `Mcp-Session-Id`, parses SSE/JSON responses, and exposes
  `call_tool()`, `list_tools()`, and typed wrappers for Blender + LLM tools.
- CLI subcommands: `tools`, `tool <name> [json args]`, `prompt <text>`,
  `interactive`.
- Root `client/` package is a compatibility shim (bootstraps `src/` on
  `sys.path`, re-exports the canonical names).

## Data flow — typical Blender tool call

1. Agent calls `blender_create_object(primitive_type="SPHERE", ...)`.
2. Server tool builds `cmd_params` and calls `_send_blender_command()`.
3. A TCP connection sends `{"type": "create_object", "params": {...}}\n`.
4. `addon.py` reads the line and `_dispatch()` queues the handler for Blender's
   main thread; the pump runs it, bpy creates the object, and the worker thread
   wakes with the result.
5. The response `{"status":"ok","result":{...}}\n` is written back; the bridge
   reads up to the newline and returns a formatted string to the MCP client.

## Data flow — AI prompt

1. Agent calls `blender_ai_prompt(prompt="...")`.
2. `_query_llm()` merges per-call overrides into `_llm_state`.
3. `llm.chat()` resolves provider → URL → payload → headers, calls the
   endpoint, and normalizes the text reply.
4. Any failure becomes an `Error: ...` string (never a stack trace).

## State management
- `_llm_state` (server) — single source of truth for the active provider
  config; mutated by `blender_set_llm_provider` and startup args.
- Add-on server state (`_server_running`, socket, thread) — Blender side only.

## Security model
- The add-on binds `localhost` by default; Blender side has no auth token in
  the single-file add-on (keep it bound to localhost or a trusted interface).
- API keys are masked in `blender_get_llm_provider` output.
- Destructive tools (`blender_delete_object`, `blender_execute_code`) carry
  `destructiveHint: true` so clients can gate them.

## Extension points
- **New LLM provider:** add a `PROVIDERS` entry (+ alias) in `llm.py`. Only
  needed when a backend doesn't speak the OpenAI chat format (style `openai`),
  Ollama's `/api/chat`, or Azure's deployment URL — most new endpoints just
  use `openai_compat`.
- **New Blender capability:** handler in `addon.py` + registration in
  `HANDLERS` + flat tool in `server.py`.
- **New MCP tool shape:** flat params; update client wrapper + tests together.

## Tests proving the layers
- `tests/test_integration.py` runs an in-process `fastmcp.Client` session
  against the server FastMCP instance (list/call tools, list/get prompts) and
  boots the real `addon.py` TCP loop on a local port to round-trip bridge
  commands (`blender_get_scene_info`, `blender_execute_code`) over a real
  socket with bpy mocked.
- `tests/test_addon.py::TestMainThreadDispatch` covers the marshalling
  contract: jobs execute on the pump thread rather than the caller, exceptions
  propagate back to the requesting thread, `WORKER_THREAD_COMMANDS` stay off the
  main thread, stopping the pump releases blocked workers, and a missing pump
  degrades to inline execution instead of deadlocking.

## Known gaps / notes
- Runtime tests mock Blender and providers; a real Blender end-to-end pass is
  still recommended before release.
- Azure model listing is intentionally unsupported (deployments are portal
  managed).
- `addon.py` doesn't yet implement auth tokens on the TCP socket; if you bind
  to non-loopback interfaces, add authentication to `_handle_client` and the
  matching token field to `_send_blender_command`.
