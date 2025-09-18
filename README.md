[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/dhakalnirajan-blender-open-mcp-badge.png)](https://mseep.ai/app/dhakalnirajan-blender-open-mcp)

# blender-open-mcp

`blender-open-mcp` is an open source project that integrates Blender with local AI models (via [Ollama](https://ollama.com/)) using the Model Context Protocol (MCP). This allows you to control Blender using natural language prompts, leveraging the power of AI to assist with 3D modeling tasks.

## Features

- **Control Blender with Natural Language:** Send prompts to a locally running Ollama model to perform actions in Blender.
- **MCP Integration:** Uses the Model Context Protocol for structured communication between the AI model and Blender.
- **Ollama Support:** Designed to work with Ollama for easy local model management.
- **Blender Add-on:** Includes a Blender add-on to provide a user interface and handle communication with the server.
- **PolyHaven Integration (Optional):** Download and use assets (HDRIs, textures, models) from [PolyHaven](https://polyhaven.com/) directly within Blender via AI prompts.
- **Basic 3D Operations:**
  - Get Scene and Object Info
  - Create Primitives
  - Modify and delete objects
  - Apply materials
- **Render Support:** Render images using the tool and retrieve information based on the output.

## Installation

### Prerequisites

1. **Blender:** Blender 3.0 or later. Download from [blender.org](https://www.blender.org/download/).
2. **Ollama:** Install from [ollama.com](https://ollama.com/), following OS-specific instructions.
3. **Python:** Python 3.10 or later.
4. **uv:** Install using `pip install uv`.
5. **Git:** Required for cloning the repository.

### Installation Steps

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/dhakalnirajan/blender-open-mcp.git
   cd blender-open-mcp
   ```

2. **Create and Activate a Virtual Environment (Recommended):**

   ```bash
   uv venv
   source .venv/bin/activate  # On Linux/macOS
   .venv\Scripts\activate  # On Windows
   ```

3. **Install Dependencies:**

   ```bash
   uv pip install -e .
   ```

4. **Install the Blender Add-on:**

   - Open Blender.
   - Go to `Edit -> Preferences -> Add-ons`.
   - Click `Install...`.
   - Select the `addon.py` file from the `blender-open-mcp` directory.
   - Enable the "Blender MCP" add-on.

5. **Download an Ollama Model (if not already installed):**

   ```bash
   ollama run llama3.2
   ```

   *(Other models like **`Gemma3`** can also be used.)*

## Setup

1. **Start the Ollama Server:** Ensure Ollama is running in the background.

2. **Start the MCP Server:**

   ```bash
   blender-mcp
   ```

   Or,

   ```bash
   python src/blender_open_mcp/server.py
   ```

   By default, it listens on `http://0.0.0.0:8000`, but you can modify settings:

   ```bash
   blender-mcp --host 127.0.0.1 --port 8001 --ollama-url http://localhost:11434 --ollama-model llama3.2
   ```

3. **Start the Blender Add-on Server:**

   - Open Blender and the 3D Viewport.
   - Press `N` to open the sidebar.
   - Find the "Blender MCP" panel.
   - Click "Start MCP Server".

## Usage

Interact with `blender-open-mcp` using the `mcp` command-line tool:

### Example Commands

- **Basic Prompt:**

  ```bash
  mcp prompt "Hello BlenderMCP!" --host http://localhost:8000
  ```

- **Get Scene Information:**

  ```bash
  mcp tool get_scene_info --host http://localhost:8000
  ```

- **Create a Cube:**

  ```bash
  mcp prompt "Create a cube named 'my_cube'." --host http://localhost:8000
  ```

- **Render an Image:**

  ```bash
  mcp prompt "Render the image." --host http://localhost:8000
  ```

- **Using PolyHaven (if enabled):**

  ```bash
  mcp prompt "Download a texture from PolyHaven." --host http://localhost:8000
  ```

## Available Tools

| Tool Name                  | Description                            | Parameters                                            |
| -------------------------- | -------------------------------------- | ----------------------------------------------------- |
| `get_scene_info`           | Retrieves scene details.               | None                                                  |
| `get_object_info`          | Retrieves information about an object. | `object_name` (str)                                   |
| `create_object`            | Creates a 3D object.                   | `type`, `name`, `location`, `rotation`, `scale`       |
| `modify_object`            | Modifies an object’s properties.       | `name`, `location`, `rotation`, `scale`, `visible`    |
| `delete_object`            | Deletes an object.                     | `name` (str)                                          |
| `set_material`             | Assigns a material to an object.       | `object_name`, `material_name`, `color`               |
| `render_image`             | Renders an image.                      | `file_path` (str)                                     |
| `execute_blender_code`     | Executes Python code in Blender.       | `code` (str)                                          |
| `get_polyhaven_categories` | Lists PolyHaven asset categories.      | `asset_type` (str)                                    |
| `search_polyhaven_assets`  | Searches PolyHaven assets.             | `asset_type`, `categories`                            |
| `download_polyhaven_asset` | Downloads a PolyHaven asset.           | `asset_id`, `asset_type`, `resolution`, `file_format` |
| `set_texture`              | Applies a downloaded texture.          | `object_name`, `texture_id`                           |
| `set_ollama_model`         | Sets the Ollama model.                 | `model_name` (str)                                    |
| `set_ollama_url`           | Sets the Ollama server URL.            | `url` (str)                                           |
| `get_ollama_models`        | Lists available Ollama models.         | None                                                  |

## Troubleshooting

If you encounter issues:

- Ensure Ollama and the `blender-open-mcp` server are running.
- Check Blender’s add-on settings.
- Verify command-line arguments.
- Refer to logs for error details.

For further assistance, visit the [GitHub Issues](https://github.com/dhakalnirajan/blender-open-mcp/issues) page.

---

Happy Blending with AI! 🚀
