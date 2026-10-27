# Blender Open MCP

Local-first **Model Context Protocol (MCP)** server for controlling a live
Blender session from AI agents, with a **provider-agnostic LLM backend**:

- **Ollama**
- **LM Studio**
- **llama.cpp** server
- **OpenAI** (and every OpenAI-compatible endpoint: vLLM, TGI, OpenRouter,
  Groq, Together, Azure AI Foundry via its OpenAI-compatible surface, …)
- any server that speaks `POST /chat/completions` with an optional base URL
  and API key

Providers are configured at startup (CLI flags or environment variables) and
can be **switched at runtime** through MCP tools, so an agent can hop between
backends without restarting the server.

```
MCP Client ──▶ FastMCP Server ──▶ (TCP 9876) ──▶ Blender add-on (addon.py) ──▶ bpy
                  │
                  └──────────────▶ (HTTP) ──▶ LLM provider (Ollama / LM Studio /
                                               llama.cpp / OpenAI-compatible / Azure)
```

---

## Repository layout

| Path | Purpose |
| --- | --- |
| `addon.py` | Single-file Blender add-on: TCP server + scene/render/PolyHaven handlers + sidebar panel |
| `src/blender_open_mcp/server.py` | MCP server: Blender tools, PolyHaven tools, LLM prompt + provider tools |
| `src/blender_open_mcp/llm.py` | Provider-agnostic LLM layer (registry, adapters, model listing) |
| `src/blender_open_mcp/client/` | Canonical async MCP client + CLI |
| `client/` | Thin compat wrapper so `from client import …` keeps working from source |
| `tests/` | pytest suites (server, client, addon) |

---

## Quick start

### 1. Install

Requires Python ≥ 3.10.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # Windows (bash/PowerShell)
# or: source .venv/bin/activate && pip install -e ".[dev]"   # macOS/Linux
```

### 2. Enable the Blender add-on

1. Open Blender.
2. `Edit → Preferences → Add-ons → Install…`, choose `addon.py`.
3. Enable **Blender MCP**.
4. In the 3D Viewport press `N`, open the **Blender MCP** tab and click
   **Start MCP Server** (listens on `localhost:9876` by default).

### 3. Start the MCP server

Default LLM backend is Ollama:

```bash
blender-mcp                          # Ollama at http://localhost:11434, model llama3.2
```

Or pick a different backend at startup:

```bash
# LM Studio (OpenAI-compatible, default localhost:1234/v1)
blender-mcp --llm-provider lmstudio --llm-model "local-model"

# llama.cpp server (default localhost:8080/v1)
blender-mcp --llm-provider llamacpp --llm-model qwen2.5-coder

# OpenAI-compatible generic endpoint
blender-mcp --llm-provider openai_compat --llm-base-url http://my-server:8000/v1 \
            --llm-api-key sk-... --llm-model my-model

# OpenAI
blender-mcp --llm-provider openai --llm-api-key "$OPENAI_API_KEY" --llm-model gpt-4o-mini

# Azure AI Foundry / Azure OpenAI
blender-mcp --llm-provider azure --llm-api-key "$AZURE_API_KEY" \
            --llm-base-url https://my-resource.openai.azure.com \
            --llm-model my-deployment \
            --llm-extra '{"resource":"my-resource","deployment":"my-deployment","api_version":"2024-06-01"}'
```

Environment variables are honored: `BLENDER_OPEN_MCP_PROVIDER`,
`BLENDER_OPEN_MCP_BASE_URL`, `BLENDER_OPEN_MCP_MODEL`,
`BLENDER_OPEN_MCP_API_KEY`.

Other useful flags: `--host`, `--port` (MCP endpoint, default `0.0.0.0:8000`),
`--blender-host`, `--blender-port`, `--transport streamable_http|http|stdio`.

### 4. Register with an MCP client

Point your MCP client at `http://localhost:8000/mcp` (streamable HTTP) or run
`blender-mcp --transport stdio`.

Example Claude/Cursor-style config:

```json
{
  "mcpServers": {
    "blender": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

---

## Available MCP tools

**Scene / object control** (forwarded to the Blender add-on over TCP):
`blender_get_scene_info`, `blender_get_object_info`, `blender_create_object`,
`blender_modify_object`, `blender_delete_object`, `blender_set_material`,
`blender_render_image`, `blender_execute_code`.

**PolyHaven assets:** `blender_get_polyhaven_categories`,
`blender_search_polyhaven_assets`, `blender_download_polyhaven_asset`,
`blender_set_texture`.

**LLM / provider control:**
- `blender_ai_prompt` – send a prompt to the active backend (per-call
  `provider`/`base_url`/`model`/`api_key` overrides supported).
- `blender_get_llm_provider` – show active provider config (API key masked).
- `blender_set_llm_provider` – switch/configure the backend at runtime.
- `blender_list_llm_models` – list models (Ollama `/api/tags` or
  OpenAI-compatible `/models`).

**Legacy aliases:** `blender_set_ollama_model`, `blender_set_ollama_url`,
`blender_get_ollama_models` keep old Ollama-only clients working.

**MCP Prompts** (`prompts/list` / `prompts/get`):
- `blender_build_scene` – guided plan for building a scene from a description.
- `blender_review_scene` – read-only inspection workflow.
- `blender_configure_llm` – provider-switching instructions with examples.

Prompts are registered in `src/blender_open_mcp/prompts.py`.

### Runtime provider switching (examples)

```text
# Switch to LM Studio
tool blender_set_llm_provider {"provider":"lmstudio","base_url":"http://localhost:1234/v1","model":"local-model"}

# Switch to llama.cpp
tool blender_set_llm_provider {"provider":"llamacpp","base_url":"http://localhost:8080/v1"}

# Back to Ollama
tool blender_set_llm_provider {"provider":"ollama","base_url":"http://localhost:11434","model":"llama3.2"}
```

#### Azure AI Foundry (worked example)

Azure's OpenAI-compatible endpoint is deployment-scoped, so three values from
your Azure AI Foundry project are required — all of them go into the `extra`
parameter, and the model you name in `model` must match the deployment name:

1. **Resource name** — in the Azure portal, open your resource (e.g.
   *Azure OpenAI* or *AI Foundry project*) and take the short name from its
   endpoint URL: `https://<resource>.openai.azure.com/...`.
2. **Deployment name** — on the *Deployments* page, e.g. `gpt-4o-mini`.
   This is what you pass as `model` (it is *not* the base model name).
3. **API key** — on the resource's *Keys and Endpoint* page.
4. **API version** (optional) — e.g. `2024-06-01` (the adapter defaults to it).

Switch to Azure at runtime with a single call (CLI form):

```bash
blender-mcp-client --host http://localhost:8000 tool blender_set_llm_provider \
  '{"provider":"azure","api_key":"YOUR_AZURE_API_KEY","model":"gpt-4o-mini",' \
  '"extra":{"resource":"my-openai-resource","deployment":"gpt-4o-mini","api_version":"2024-06-01"}}'
```

The same call through the Python API:

```python
import asyncio
from blender_open_mcp.client.client import BlenderMCPClient

async def main():
    async with BlenderMCPClient("http://localhost:8000") as c:
        # Switch to Azure AI Foundry
        print(await c.set_llm_provider(
            provider="azure",
            api_key="YOUR_AZURE_API_KEY",
            model="gpt-4o-mini",
            extra={
                "resource": "my-openai-resource",
                "deployment": "gpt-4o-mini",
                "api_version": "2024-06-01",
            },
        ))
        # Confirm the active config (API key is masked)
        print(await c.get_llm_provider())
        # Use it
        print(await c.ai_prompt("Create a red cube at the origin"))

asyncio.run(main())
```

What the adapter does with those values — it builds the deployment-scoped
request and sends the key in the `api-key` header:

```
POST https://my-openai-resource.openai.azure.com/openai/deployments/gpt-4o-mini/chat/completions?api-version=2024-06-01
api-key: YOUR_AZURE_API_KEY
{"model": "gpt-4o-mini", "messages": [...], "stream": false}
```

Notes:
- You may omit `base_url` entirely (the placeholder
  `https://RESOURCE.openai.azure.com` is filled in from `extra.resource`) or
  pass the full base URL explicitly.
- If you use an AI Foundry **serverless model endpoint** (the
  `*.services.ai.azure.com/models` surface) instead of a deployment-scoped
  resource, point `provider` at the generic OpenAI-compatible adapter with the
  serverless base URL: `provider="openai_compat"`,
  `base_url="https://<resource>.services.ai.azure.com/models"`.
- The same configuration can be applied at startup instead of at runtime:

```bash
blender-mcp --llm-provider azure \
  --llm-api-key "$AZURE_API_KEY" \
  --llm-model gpt-4o-mini \
  --llm-extra '{"resource":"my-openai-resource","deployment":"gpt-4o-mini","api_version":"2024-06-01"}'
```
```

---

## Client CLI

```bash
blender-mcp-client --host http://localhost:8000 tools
blender-mcp-client --host http://localhost:8000 tool blender_get_scene_info
blender-mcp-client --host http://localhost:8000 tool blender_set_llm_provider '{"provider":"lmstudio"}'
blender-mcp-client --host http://localhost:8000 prompt "Create a metallic sphere at 0,0,2"
blender-mcp-client --host http://localhost:8000 interactive
```

As a library:

```python
import asyncio
from blender_open_mcp.client.client import BlenderMCPClient

async def main():
    async with BlenderMCPClient("http://localhost:8000") as c:
        print(await c.get_scene_info())
        await c.create_object("SPHERE", location=(0, 0, 2))
        print(await c.ai_prompt("What should I build next?"))

asyncio.run(main())
```

---

## Provider layer internals

`src/blender_open_mcp/llm.py` keeps a registry of provider specs and routes
every request through one chat helper:

- **OpenAI-style** providers post to `<base_url>/chat/completions` with a
  `Bearer` token when an API key is set, and parse
  `choices[0].message.content`.
- **Ollama** posts to `/api/chat` natively (or to its `/v1/chat/completions`
  surface when the base URL ends in `/v1`).
- **Azure** posts to
  `https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions?api-version=…`
  using the `api-key` header.
- **Model listing**: Ollama `/api/tags`, others `/models` (Azure deployments
  are managed in the portal and not listed).

Add new backends by inserting an entry in `PROVIDERS` (plus an alias in
`PROVIDER_ALIASES`); nothing else changes.

---

## Development

```bash
.venv/Scripts/python -m pytest tests -q
```

The suites mock `bpy` (addon tests), `httpx` (provider/PolyHaven routing),
and the MCP client wire format, so they run without Blender or a live LLM.

`tests/test_integration.py` additionally proves the layers work together:

- an in-process `fastmcp.Client` session lists tools/prompts and calls
  provider tools over the real MCP protocol;
- the actual `addon.py` TCP server loop is started on a local port (bpy
  mocked) and driven through the MCP server's Blender bridge, so
  `blender_get_scene_info` and `blender_execute_code` round-trip over a real
  socket;
- a parity test pins every bridge command to a registered addon handler and
  every `blender_*` tool to a registered MCP tool;
- `TestMainThreadDispatch` in `tests/test_addon.py` covers the add-on's
  main-thread marshalling: bpy handlers run on the timer pump rather than the
  connection thread, errors propagate back to the caller, network-only commands
  stay on the worker thread, and a stopped pump releases blocked workers.

## Notes / known gaps

- Tests exercise the server without a live Blender; run them against a real
  Blender session to validate `addon.py` end to end.
- Blender API calls are executed on Blender's main thread via a
  `bpy.app.timers` pump, so scene commands are serialised: a long render or a
  heavy `blender_execute_code` build delays whatever is queued behind it.
  PolyHaven downloads and LLM calls stay on worker threads and don't block the
  UI. If Blender is busy with a modal operator, commands wait rather than
  running against an invalid context.
- See `ARCHITECTURE.md` for the component diagram and `AGENTS.md` for
  contributor conventions.
