# server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Optional
import httpx
from io import BytesIO
import base64
import argparse
import os
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: Optional[socket.socket] = None
    timeout: float = 15.0  # Added timeout as a property

    def __post_init__(self):
         if not isinstance(self.host, str):
             raise ValueError("Host must be a string")
         if not isinstance(self.port, int):
             raise ValueError("Port must be an int")

    def connect(self) -> bool:
        if self.sock:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            self.sock.settimeout(self.timeout) # Set timeout on socket
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {e!s}")
            self.sock = None
            return False

    def disconnect(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting: {e!s}")
            finally:
                self.sock = None

    def _receive_full_response(self, buffer_size: int = 8192) -> bytes:
        """Receive data with timeout using a loop."""
        chunks: List[bytes] = []
        timed_out = False
        try:
            while True:
                try:
                    chunk = self.sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            # Requirement 1b
                            raise Exception("Connection closed by Blender before any data was sent in this response")
                        else:
                            # Requirement 1a
                            raise Exception("Connection closed by Blender mid-stream with incomplete JSON data")
                    chunks.append(chunk)
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))  # Check if it is valid json
                        logger.debug(f"Received response ({len(data)} bytes)")
                        return data # Complete JSON received
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    logger.warning("Socket timeout during receive")
                    timed_out = True # Set flag
                    break # Stop listening to socket
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error: {e!s}")
                    self.sock = None
                    raise # re-raise to outer error handler
            
            # This part is reached if loop is broken by 'break' (only timeout case now)
            if timed_out:
                if chunks:
                    data = b''.join(chunks)
                    # Check if the partial data is valid JSON (it shouldn't be if timeout happened mid-stream)
                    try:
                        json.loads(data.decode('utf-8'))
                        # This case should ideally not be hit if JSON was incomplete,
                        # but if it's somehow valid, return it.
                        logger.warning("Timeout occurred, but received data forms valid JSON.")
                        return data
                    except json.JSONDecodeError:
                        # Requirement 2a
                        raise Exception(f"Incomplete JSON data received before timeout. Received: {data[:200]}")
                else:
                    # Requirement 2b
                    raise Exception("Timeout waiting for response, no data received.")
            
            # Fallback if loop exited for a reason not covered by explicit raises inside or by timeout logic
            # This should ideally not be reached with the current logic.
            if chunks: # Should have been handled by "Connection closed by Blender mid-stream..."
                data = b''.join(chunks)
                logger.warning(f"Exited receive loop unexpectedly with data: {data[:200]}")
                raise Exception("Receive loop ended unexpectedly with partial data.")
            else: # Should have been handled by "Connection closed by Blender before any data..." or timeout
                logger.warning("Exited receive loop unexpectedly with no data.")
                raise Exception("Receive loop ended unexpectedly with no data.")

        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            # This handles connection errors raised from within the loop or if self.sock.recv fails
            logger.error(f"Connection error during receive: {e!s}")
            self.sock = None # Ensure socket is reset
            # Re-raise with a more specific message if needed, or just re-raise
            raise Exception(f"Connection to Blender lost during receive: {e!s}")
        except Exception as e: 
            # Catch other exceptions, including our custom ones, and log them
            logger.error(f"Error during _receive_full_response: {e!s}")
            # If it's not one of the specific connection errors, it might be one of our custom messages
            # or another unexpected issue. Re-raise to be handled by send_command.
            raise


    def send_command(self, command_type: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
         if not self.sock and not self.connect():
            raise ConnectionError("Not connected")
         command = {"type": command_type, "params": params or {}}
         try:
              logger.info(f"Sending command: {command_type} with params: {params}")
              self.sock.sendall(json.dumps(command).encode('utf-8'))
              logger.info(f"Command sent, waiting for response...")
              response_data = self._receive_full_response()
              logger.debug(f"Received response ({len(response_data)} bytes)")
              response = json.loads(response_data.decode('utf-8'))
              logger.info(f"Response status: {response.get('status', 'unknown')}")
              if response.get("status") == "error":
                 logger.error(f"Blender error: {response.get('message')}")
                 raise Exception(response.get("message", "Unknown Blender error"))
              return response.get("result", {})

         except socket.timeout:
             logger.error("Socket timeout from Blender")
             self.sock = None # reset socket connection
             raise Exception("Timeout waiting for Blender - simplify request")
         except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
             logger.error(f"Socket connection error: {e!s}")
             self.sock = None # reset socket connection
             raise Exception(f"Connection to Blender lost: {e!s}")
         except json.JSONDecodeError as e:
             logger.error(f"Invalid JSON response: {e!s}")
             if 'response_data' in locals() and response_data:
                logger.error(f"Raw (first 200): {response_data[:200]}")
             raise Exception(f"Invalid response from Blender: {e!s}")
         except Exception as e:
              logger.error(f"Error communicating with Blender: {e!s}")
              self.sock = None # reset socket connection
              raise Exception(f"Communication error: {e!s}")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    logger.info("BlenderMCP server starting up")
    try:
        blender = get_blender_connection()
        logger.info("Connected to Blender on startup")
    except Exception as e:
        logger.warning(f"Could not connect to Blender on startup: {e!s}")
        logger.warning("Ensure Blender addon is running before using resources")
    yield {}
    global _blender_connection
    if _blender_connection:
        logger.info("Disconnecting from Blender on shutdown")
        _blender_connection.disconnect()
        _blender_connection = None
    logger.info("BlenderMCP server shut down")

# Initialize MCP server instance globally
mcp = FastMCP(
    "BlenderOpenMCP",
    lifespan=server_lifespan
)

_blender_connection = None
_polyhaven_enabled = False
# Default values (will be overridden by command-line arguments)
_ollama_model = ""
_ollama_url = "http://localhost:11434"

def get_blender_connection() -> BlenderConnection:
    global _blender_connection, _polyhaven_enabled
    if _blender_connection:
        try:
            result = _blender_connection.send_command("get_polyhaven_status")
            _polyhaven_enabled = result.get("enabled", False)
            return _blender_connection
        except Exception as e:
            logger.warning(f"Existing connection invalid: {e!s}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None
    if _blender_connection is None:
        _blender_connection = BlenderConnection(host="localhost", port=9876)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Addon running?")
        logger.info("Created new persistent connection to Blender")
    return _blender_connection


async def query_ollama(prompt: str, context: Optional[List[Dict]] = None, image: Optional[Image] = None) -> str:
    global _ollama_model, _ollama_url

    payload = {"prompt": prompt, "model": _ollama_model, "format": "json", "stream": False}
    if context:
        payload["context"] = context
    if image:
        if image.data:
            payload["images"] = [image.data]
        elif image.path:
            try:
                with open(image.path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                payload["images"] = [encoded_string]
            except FileNotFoundError:
                logger.error(f"Image file not found: {image.path}")
                return "Error: Image file not found."
        else:
            logger.warning("Image without data or path. Ignoring.")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{_ollama_url}/api/generate", json=payload, timeout=60.0)
            response.raise_for_status()  # Raise HTTPStatusError for bad status
            response_data = response.json()
            logger.debug(f"Raw Ollama response: {response_data}")
            if "response" in response_data:
                return response_data["response"]
            else:
                logger.error(f"Unexpected response format: {response_data}")
                return "Error: Unexpected response format from Ollama."

    except httpx.HTTPStatusError as e:
        logger.error(f"Ollama API error: {e.response.status_code} - {e.response.text}")
        return f"Error: Ollama API returned: {e.response.status_code}"
    except httpx.RequestError as e:
        logger.error(f"Ollama API request failed: {e}")
        return "Error: Failed to connect to Ollama API."
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e!s}")
        return f"Error: An unexpected error occurred: {e!s}"

@mcp.prompt()
async def base_prompt(context: Context, user_message: str) -> str:
    system_message = f"""You are a helpful assistant that controls Blender.
    You can use the following tools. Respond in well-formatted, valid JSON:
    {mcp.tools_schema()}"""
    full_prompt = f"{system_message}\n\n{user_message}"
    response = await query_ollama(full_prompt, context.history(), context.get_image())
    return response

@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")
        return json.dumps(result, indent=2)  # Return as a formatted string
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        return json.dumps(result, indent=2)  # Return as a formatted string
    except Exception as e:
        return f"Error: {e!s}"
    
@mcp.tool()
def create_object(
    ctx: Context,
    type: str = "CUBE",
    name: Optional[str] = None,
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None
) -> str:
    try:
        blender = get_blender_connection()
        loc, rot, sc = location or [0, 0, 0], rotation or [0, 0, 0], scale or [1, 1, 1]
        params = {"type": type, "location": loc, "rotation": rot, "scale": sc}
        if name: params["name"] = name
        result = blender.send_command("create_object", params)
        return f"Created {type} object: {result['name']}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def modify_object(
    ctx: Context,
    name: str,
    location: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
    visible: Optional[bool] = None
) -> str:
    try:
        blender = get_blender_connection()
        params = {"name": name}
        if location is not None: params["location"] = location
        if rotation is not None: params["rotation"] = rotation
        if scale is not None: params["scale"] = scale
        if visible is not None: params["visible"] = visible
        result = blender.send_command("modify_object", params)
        return f"Modified object: {result['name']}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def delete_object(ctx: Context, name: str) -> str:
    try:
        blender = get_blender_connection()
        blender.send_command("delete_object", {"name": name})
        return f"Deleted object: {name}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def set_material(
    ctx: Context,
    object_name: str,
    material_name: Optional[str] = None,
    color: Optional[List[float]] = None
) -> str:
    try:
        blender = get_blender_connection()
        params = {"object_name": object_name}
        if material_name: params["material_name"] = material_name
        if color: params["color"] = color
        result = blender.send_command("set_material", params)
        return f"Applied material to {object_name}: {result.get('material_name', 'unknown')}"
    except Exception as e:
        return f"Error: {e!s}"
    
@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return f"Code executed: {result.get('result', '')}"
    except Exception as e:
        return f"Error: {e!s}"
    
@mcp.tool()
def get_polyhaven_categories(ctx: Context, asset_type: str = "hdris") -> str:
    try:
        blender = get_blender_connection()
        if not _polyhaven_enabled: return "PolyHaven disabled."
        result = blender.send_command("get_polyhaven_categories", {"asset_type": asset_type})
        if "error" in result: return f"Error: {result['error']}"
        categories = result["categories"]
        formatted = f"Categories for {asset_type}:\n" + \
                    "\n".join(f"- {cat}: {count}" for cat, count in
                      sorted(categories.items(), key=lambda x: x[1], reverse=True))
        return formatted
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def search_polyhaven_assets(ctx: Context, asset_type: str = "all", categories: Optional[str] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("search_polyhaven_assets",
                {"asset_type": asset_type, "categories": categories})
        if "error" in result: return f"Error: {result['error']}"
        assets, total, returned = result["assets"], result["total_count"], result["returned_count"]
        formatted = f"Found {total} assets" + (f" in: {categories}" if categories else "") + \
                    f"\nShowing {returned}:\n" + "".join(
            f"- {data.get('name', asset_id)} (ID: {asset_id})\n"
            f"  Type: {['HDRI', 'Texture', 'Model'][data.get('type', 0)]}\n"
            f"  Categories: {', '.join(data.get('categories', []))}\n"
            f"  Downloads: {data.get('download_count', 'Unknown')}\n"
            for asset_id, data in sorted(assets.items(),
                                        key=lambda x: x[1].get("download_count", 0),
                                        reverse=True))
        return formatted
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def download_polyhaven_asset(ctx: Context, asset_id: str, asset_type: str,
                             resolution: str = "1k", file_format: Optional[str] = None) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("download_polyhaven_asset", {
            "asset_id": asset_id, "asset_type": asset_type,
            "resolution": resolution, "file_format": file_format})
        if "error" in result: return f"Error: {result['error']}"
        if result.get("success"):
            message = result.get("message", "Success")
            if asset_type == "hdris": return f"{message}. HDRI set as world."
            elif asset_type == "textures":
                mat_name, maps = result.get("material", ""), ", ".join(result.get("maps", []))
                return f"{message}. Material '{mat_name}' with: {maps}."
            elif asset_type == "models": return f"{message}. Model imported."
            return message
        return f"Failed: {result.get('message', 'Unknown')}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def set_texture(ctx: Context, object_name: str, texture_id: str) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_texture",
                                     {"object_name": object_name, "texture_id": texture_id})
        if "error" in result: return f"Error: {result['error']}"
        if result.get("success"):
            mat_name, maps = result.get("material", ""), ", ".join(result.get("maps", []))
            info, nodes = result.get("material_info", {}), result.get("material_info", {}).get("texture_nodes", [])
            output = (f"Applied '{texture_id}' to {object_name}.\nMaterial '{mat_name}': {maps}.\n"
                      f"Nodes: {info.get('has_nodes', False)}\nCount: {info.get('node_count', 0)}\n")
            if nodes:
                output += "Texture nodes:\n" + "".join(
                    f"- {node['name']} ({node['image']})\n" +
                    ("  Connections:\n" + "".join(f"    {conn}\n" for conn in node['connections'])
                     if node['connections'] else "")
                    for node in nodes)
            return output
        return f"Failed: {result.get('message', 'Unknown')}"
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
def get_polyhaven_status(ctx: Context) -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_polyhaven_status")
        return result.get("message", "")  # Return the message directly
    except Exception as e:
        return f"Error: {e!s}"

@mcp.tool()
async def set_ollama_model(ctx: Context, model_name: str) -> str:
    global _ollama_model
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{_ollama_url}/api/show",
                                         json={"name": model_name}, timeout=10.0)
            if response.status_code == 200:
                _ollama_model = model_name
                return f"Ollama model set to: {_ollama_model}"
            else: return f"Error: Could not find model '{model_name}'."
    except Exception as e:
        return f"Error: Failed to communicate: {e!s}"

@mcp.tool()
async def set_ollama_url(ctx: Context, url: str) -> str:
    global _ollama_url
    if not (url.startswith("http://") or url.startswith("https://")):
        return "Error: Invalid URL format. Must start with http:// or https://."
    _ollama_url = url
    return f"Ollama URL set to: {_ollama_url}"

@mcp.tool()
async def get_ollama_models(ctx: Context) -> str:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{_ollama_url}/api/tags", timeout=10.0)
            response.raise_for_status()
            models_data = response.json()
            if "models" in models_data:
                model_list = [model["name"] for model in models_data["models"]]
                return "Available Ollama models:\n" + "\n".join(model_list)
            else: return "Error: Unexpected response from Ollama /api/tags."
    except httpx.HTTPStatusError as e:
        return f"Error: Ollama API error: {e.response.status_code}"
    except httpx.RequestError as e:
        return "Error: Failed to connect to Ollama API."
    except Exception as e:
        return f"Error: An unexpected error: {e!s}"

@mcp.tool()
async def render_image(ctx: Context, file_path: str = "render.png") -> str:
    try:
        blender = get_blender_connection()
        result = blender.send_command("render_scene", {"output_path": file_path})
        if result and result.get("rendered"):
            # Use the actual output path returned from Blender
            actual_file_path = result.get("output_path")
            if not actual_file_path:
                return "Error: Blender rendered but did not return an output path."
            try:
                with open(actual_file_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    ctx.add_image(Image(data=encoded_string))  # Add image to the context
                    return "Image Rendered Successfully."
            except FileNotFoundError:
                return f"Error: Blender rendered to '{actual_file_path}', but the file was not found by the server."
            except Exception as exception:
                return f"Blender rendered, but the image could not be read: {exception!s}"
        else:
            return f"Error: Rendering failed with result: {result}"
    except Exception as e:
        return f"Error: {e!s}"

def main():
    """Run the MCP server."""
    parser = argparse.ArgumentParser(description="BlenderMCP Server")
    # Set global variables from command-line arguments
    global _ollama_url, _ollama_model

    parser.add_argument("--ollama-url", type=str, default=_ollama_url,
                        help="URL of the Ollama server")
    parser.add_argument("--ollama-model", type=str, default=_ollama_model,
                        help="Default Ollama model to use")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port for the MCP server to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="Host for the MCP server to listen on")

    args = parser.parse_args()

    _ollama_url = args.ollama_url
    _ollama_model = args.ollama_model

    # MCP instance is already created globally
    mcp.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()