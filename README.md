[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/dhakalnirajan-blender-open-mcp-badge.png)](https://mseep.ai/app/dhakalnirajan-blender-open-mcp)

# blender-open-mcp

**Open Models MCP for Blender3D using Ollama**

Control Blender 3D with natural language prompts via local AI models. Built on the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), connecting Claude, Cursor, or any MCP client to Blender through a local Ollama LLM.

---

## Architecture

```
MCP Client (Claude/Cursor/CLI)
         │ HTTP / stdio
         ▼
┌─────────────────────┐
│   FastMCP Server    │  ← server.py  (port 8000)
│   blender-open-mcp  │
└─────────────────────┘
    │ TCP socket           │ HTTP
    ▼                      ▼
┌──────────────┐    ┌─────────────┐
│  Blender     │    │   Ollama    │  (port 11434)
│  Add-on      │    │  llama3.2   │
│  addon.py    │    │  gemma3...  │
│  (port 9876) │    └─────────────┘
└──────────────┘
       │ bpy
       ▼
  Blender Python API
```

Three independent processes:

- **FastMCP Server** (`server.py`): Exposes MCP tools over HTTP or stdio
- **Blender Add-on** (`addon.py`): TCP socket server running inside Blender
- **Ollama**: Local LLM serving natural language queries

---

## Installation

### Prerequisites

| Dependency | Version | Install |
|-----------|---------|---------|
| Blender | 3.0+ | [blender.org](https://www.blender.org/download/) |
| Python | 3.10+ | System or [python.org](https://www.python.org/) |
| Ollama | Latest | [ollama.com](https://ollama.com/) |
| uv | Latest | `pip install uv` |

### 1. Clone and set up

```bash
git clone https://github.com/dhakalnirajan/blender-open-mcp.git
cd blender-open-mcp

# Create virtual environment and install
uv venv
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows

uv pip install -e .
```

### 2. Install the Blender Add-on

1. Open Blender
2. Go to **Edit → Preferences → Add-ons → Install...**
3. Select `addon.py` from the repository root
4. Enable **"Blender MCP"**
5. Open the **3D Viewport**, press **N**, find the **Blender MCP** panel
6. Click **"Start MCP Server"** (default port: 9876)

### 3. Pull an Ollama model

   ```bash
   ollama pull ollama run llama3.2
   ```

   *(Other models like **`Gemma3`** can also be used.)*

## Setup

1. **Start the Ollama Server:** Ensure Ollama is running in the background.

2. **Start the MCP Server:**

```bash
blender-mcp
```

Custom options:

```bash
blender-mcp \
  --host 127.0.0.1 \
  --port 8000 \
  --blender-host localhost \
  --blender-port 9876 \
  --ollama-url http://localhost:11434 \
  --ollama-model llama3.2
```

For stdio transport (Claude Desktop, Cursor):

```bash
blender-mcp --transport stdio
```

---

## Usage

### MCP Client CLI

```bash
# Interactive shell
blender-mcp-client interactive

# One-shot scene info
blender-mcp-client scene

# Call a specific tool
blender-mcp-client tool blender_get_scene_info
blender-mcp-client tool blender_create_object '{"primitive_type": "SPHERE", "name": "MySphere"}'

# Natural language prompt
blender-mcp-client prompt "Create a metallic sphere at position 0, 0, 2"

# List all available tools
blender-mcp-client tools
```

### Python API

```python
import asyncio
from client.client import BlenderMCPClient

async def demo():
    async with BlenderMCPClient("http://localhost:8000") as client:
        # Scene inspection
        print(await client.get_scene_info())

        # Create objects
        await client.create_object("CUBE", name="MyCube", location=(0, 0, 0))
        await client.create_object("SPHERE", name="MySphere", location=(3, 0, 0))

        # Apply materials
        await client.set_material("MyCube", "GoldMat", color=[1.0, 0.84, 0.0, 1.0])

        # Move objects
        await client.modify_object("MySphere", location=(3, 0, 2), scale=(1.5, 1.5, 1.5))

        # PolyHaven assets
        categories = await client.get_polyhaven_categories("textures")
        await client.download_polyhaven_asset("brick_wall_001", resolution="2k")
        await client.set_texture("MyCube", "brick_wall_001")

        # Render
        await client.render_image("/tmp/my_render.png")

        # AI assistance
        response = await client.ai_prompt(
            "Write bpy code to add a sun light pointing down"
        )
        print(response)

        # Execute the generated code
        await client.execute_code(response)

asyncio.run(demo())
```

### Claude Desktop / Cursor Integration

Add to your `mcp.json` (or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "blender-open-mcp": {
      "command": "blender-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

---

## Available Tools

| Tool | Description | Modifies Blender |
|------|-------------|-----------------|
| `blender_get_scene_info` | Full scene summary: objects, camera, render settings | No |
| `blender_get_object_info` | Detailed object info: transforms, materials, mesh stats | No |
| `blender_create_object` | Add a primitive mesh (CUBE, SPHERE, CYLINDER, ...) | Yes |
| `blender_modify_object` | Change location, rotation, scale, visibility | Yes |
| `blender_delete_object` | Remove an object from the scene | Yes ⚠️ |
| `blender_set_material` | Create and assign a Principled BSDF material | Yes |
| `blender_render_image` | Render current scene to a file | Yes |
| `blender_execute_code` | Run arbitrary Python/bpy code in Blender | Yes ⚠️ |
| `blender_get_polyhaven_categories` | List PolyHaven asset categories | No |
| `blender_search_polyhaven_assets` | Search PolyHaven library with pagination | No |
| `blender_download_polyhaven_asset` | Download & import a PolyHaven asset | Yes |
| `blender_set_texture` | Apply a downloaded PolyHaven texture to an object | Yes |
| `blender_ai_prompt` | Send a natural language prompt to Ollama | No |
| `blender_get_ollama_models` | List available local Ollama models | No |
| `blender_set_ollama_model` | Switch the active Ollama model | No |
| `blender_set_ollama_url` | Update the Ollama server URL | No |

---

## Default Ports

| Service | Port |
|---------|------|
| FastMCP Server | 8000 |
| Blender Add-on (TCP) | 9876 |
| Ollama | 11434 |

---

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Type checking
mypy src/

# Linting
ruff check src/ client/
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Cannot connect to Blender add-on` | Open Blender → N-sidebar → Blender MCP → **Start MCP Server** |
| `Cannot connect to Ollama` | Run `ollama serve` in a terminal |
| `Object not found` | Check exact object name via `blender_get_scene_info` |
| `Render fails` | Ensure the output directory exists and is writable |
| `PolyHaven download fails` | Check internet connection; try a lower resolution |

---

## License

MIT License. See [LICENSE](LICENSE) for details.

This project is not affiliated with the Blender Foundation.
