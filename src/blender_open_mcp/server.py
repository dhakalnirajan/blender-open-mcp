"""
blender_open_mcp - MCP Server for Blender3D with provider-agnostic AI backends.

Architecture:
  - FastMCP server (default port 8000): exposes tools to MCP clients.
  - Blender add-on socket (default port 9876): TCP server inside Blender
    that executes scene commands (addon.py).
  - LLM backends: OpenAI-compatible REST APIs (OpenAI, Azure AI Foundry,
    LM Studio, llama.cpp server, vLLM, ...), Ollama native API, and any
    endpoint reachable via a base URL + optional API key.

Communication flow:
  MCP Client -> FastMCP Server -> (TCP) -> Blender Add-on -> bpy execution
  MCP Client -> FastMCP Server -> (HTTP) -> LLM provider -> assistant reply

Providers can be configured at startup (CLI/env) and switched at runtime via
the blender_set_llm_provider / blender_list_llm_models MCP tools.

All tool signatures are flat (no wrapper objects) so any standard MCP client
can call them with top-level arguments.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import llm as llm_backend
from .llm import ProviderError, resolve_provider

# ---------------------------------------------------------------------------
# Logging – use stderr so it doesn't pollute stdio transport
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("blender_mcp")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BLENDER_HOST: str = "localhost"
BLENDER_PORT: int = 9876
BLENDER_TIMEOUT: float = 30.0

DEFAULT_LLM_PROVIDER: str = os.environ.get("BLENDER_OPEN_MCP_PROVIDER", "ollama")
DEFAULT_LLM_URL: str = os.environ.get(
    "BLENDER_OPEN_MCP_BASE_URL", "http://localhost:11434"
)
DEFAULT_LLM_MODEL: str = os.environ.get(
    "BLENDER_OPEN_MCP_MODEL", "llama3.2"
)
DEFAULT_LLM_API_KEY: Optional[str] = os.environ.get("BLENDER_OPEN_MCP_API_KEY")

POLYHAVEN_API_BASE: str = "https://api.polyhaven.com"

DEFAULT_SYSTEM_PROMPT: str = (
    "You are an expert Blender 3D artist and Python developer. "
    "You help users control Blender using the bpy Python API. "
    "Provide concise, runnable Python code examples when appropriate."
)

# ---------------------------------------------------------------------------
# Runtime LLM state (mutable at runtime via blender_set_llm_provider)
# ---------------------------------------------------------------------------
_llm_state: Dict[str, Any] = {
    "provider": DEFAULT_LLM_PROVIDER,
    "base_url": DEFAULT_LLM_URL,
    "model": DEFAULT_LLM_MODEL,
    "api_key": DEFAULT_LLM_API_KEY,
    "extra": {},
}

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "blender_open_mcp",
    instructions=(
        "Control a live Blender session through the Model Context Protocol and "
        "route natural-language prompts to any LLM backend (OpenAI-compatible, "
        "Ollama, LM Studio, llama.cpp, Azure). Use blender_get_scene_info first, "
        "then blender_get_llm_provider / blender_set_llm_provider to configure "
        "the AI backend."
    ),
)


# ===========================================================================
# Shared helpers
# ===========================================================================

def _send_blender_command(
    command_type: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Send a JSON command to the Blender add-on TCP server and return the response.

    Raises:
        ConnectionRefusedError: if Blender add-on is not running.
        TimeoutError: if the command takes too long.
        ValueError: if the response cannot be parsed.
    """
    payload = json.dumps({"type": command_type, "params": params or {}}) + "\n"
    raw = ""
    try:
        with socket.create_connection(
            (BLENDER_HOST, BLENDER_PORT), timeout=BLENDER_TIMEOUT
        ) as sock:
            sock.sendall(payload.encode("utf-8"))
            # Responses are newline-terminated (see addon.py _ok/_err), so stop
            # at the first "\n" instead of waiting for the peer to close. Waiting
            # for EOF cannot tell a clean shutdown apart from a crash mid-reply.
            buffer = bytearray()
            while b"\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    break  # peer closed; fall through with whatever arrived
                buffer.extend(chunk)
            raw = buffer.decode("utf-8").strip()
        if not raw:
            raise ValueError(
                "Blender add-on closed the connection without sending a response. "
                "Check Blender's system console for a traceback."
            )
        response: Dict[str, Any] = json.loads(raw)
        return response
    except ConnectionRefusedError:
        raise ConnectionRefusedError(
            f"Cannot connect to Blender add-on at {BLENDER_HOST}:{BLENDER_PORT}. "
            "Make sure Blender is open with the Blender MCP add-on enabled and the "
            "server started (N-key sidebar -> Blender MCP -> Start MCP Server)."
        )
    except (ConnectionResetError, BrokenPipeError) as exc:
        raise ConnectionError(
            f"Blender reset the connection while handling '{command_type}' "
            f"({type(exc).__name__}). Blender may have crashed or become "
            "unresponsive; check its system console and restart the MCP server "
            "from the sidebar."
        )
    except socket.timeout:
        raise TimeoutError(
            f"Blender add-on did not respond within {BLENDER_TIMEOUT}s. "
            "The operation may still be running in Blender."
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON response from Blender: {exc}. Raw: {raw!r}")


def _format_blender_result(response: Dict[str, Any]) -> str:
    """Convert a Blender TCP response into a human-readable string."""
    if response.get("status") == "error":
        return f"Error from Blender: {response.get('message', 'Unknown error')}"
    result = response.get("result", response)
    if isinstance(result, (dict, list)):
        return json.dumps(result, indent=2)
    return str(result)


def _handle_blender_error(exc: Exception) -> str:
    """Produce a friendly, actionable error string from common exceptions."""
    # ConnectionError covers refused/reset/broken-pipe alike.
    if isinstance(exc, (ConnectionError, TimeoutError, ValueError)):
        return str(exc)
    return (
        f"Unexpected error communicating with Blender: {type(exc).__name__}: {exc}. "
        "Check the server logs for details."
    )


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _mask_api_key(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def _handle_provider_error(exc: Exception) -> str:
    """Convert provider-layer errors into friendly, actionable strings."""
    if isinstance(exc, ProviderError):
        return f"Error: {exc}"
    return (
        f"Error calling LLM provider: {type(exc).__name__}: {exc}. "
        "Check blender_get_llm_provider for the active configuration."
    )


async def _query_llm(
    prompt: str,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Send a prompt to the configured (or explicitly requested) LLM provider.

    Missing fields fall back to the current runtime state (_llm_state).
    Returns the assistant text, or an error string prefixed with "Error:".
    """
    provider_key = provider or _llm_state["provider"]
    url = base_url or _llm_state["base_url"]
    key = api_key if api_key is not None else _llm_state["api_key"]
    model_name = model or _llm_state["model"]
    extra_cfg = {**_llm_state.get("extra", {}), **(extra or {})}

    system = system_prompt or DEFAULT_SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    try:
        return await llm_backend.chat(
            messages,
            provider=provider_key,
            base_url=url,
            api_key=key,
            model=model_name,
            extra=extra_cfg,
        )
    except Exception as exc:
        return _handle_provider_error(exc)


def _redact_state() -> Dict[str, Any]:
    return {
        "provider": _llm_state["provider"],
        "base_url": _llm_state["base_url"],
        "model": _llm_state["model"],
        "api_key": _mask_api_key(_llm_state.get("api_key")),
        "extra": _llm_state.get("extra", {}),
        "supported_providers": sorted(llm_backend.PROVIDERS),
    }


def _apply_provider_config(
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    extra: Optional[Dict[str, Any]],
) -> None:
    """Update runtime LLM state from tool/CLI arguments (partial update)."""
    if provider:
        _llm_state["provider"] = resolve_provider(provider)
    if base_url:
        _llm_state["base_url"] = base_url.rstrip("/")
    if api_key is not None:
        _llm_state["api_key"] = api_key
    if model:
        _llm_state["model"] = model
    if extra is not None:
        _llm_state["extra"] = dict(extra)


# ===========================================================================
# Input models (Pydantic v2) — used for shared validation helpers
# ===========================================================================

class ResponseFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"


class Vec3(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    x: float = Field(default=0.0, description="X coordinate")
    y: float = Field(default=0.0, description="Y coordinate")
    z: float = Field(default=0.0, description="Z coordinate")

    def as_list(self) -> List[float]:
        return [self.x, self.y, self.z]


class PrimitiveType(str, Enum):
    CUBE = "CUBE"
    SPHERE = "SPHERE"
    CYLINDER = "CYLINDER"
    CONE = "CONE"
    TORUS = "TORUS"
    PLANE = "PLANE"
    CIRCLE = "CIRCLE"
    ICO_SPHERE = "ICO_SPHERE"
    GRID = "GRID"
    MONKEY = "MONKEY"


class PolyHavenAssetType(str, Enum):
    HDRIS = "hdris"
    TEXTURES = "textures"
    MODELS = "models"
    ALL = "all"


def _vec3_arg(value: Optional[Vec3]) -> Optional[List[float]]:
    return value.as_list() if value is not None else None


def _enum_value(value: Any) -> str:
    """Return the string value of an enum or plain string argument."""
    return value.value if hasattr(value, "value") else str(value)


# ===========================================================================
# MCP Tools — Scene & Object Management
# ===========================================================================

@mcp.tool(
    name="blender_get_scene_info",
    annotations={
        "title": "Get Blender Scene Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_get_scene_info() -> str:
    """Retrieve a full summary of the current Blender scene (objects, camera, render settings)."""
    try:
        return _format_blender_result(_send_blender_command("get_scene_info"))
    except Exception as exc:
        return _handle_blender_error(exc)


@mcp.tool(
    name="blender_get_object_info",
    annotations={
        "title": "Get Blender Object Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_get_object_info(
    object_name: str,
    response_format: str = "markdown",
) -> str:
    """Retrieve detailed information about a specific Blender object by name."""
    try:
        result = _format_blender_result(
            _send_blender_command("get_object_info", {"object_name": object_name})
        )
        if response_format == ResponseFormat.MARKDOWN.value:
            return f"## Object: {object_name}\n\n```json\n{result}\n```"
        return result
    except Exception as exc:
        return _handle_blender_error(exc)


@mcp.tool(
    name="blender_create_object",
    annotations={
        "title": "Create Blender 3D Object",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def blender_create_object(
    primitive_type: PrimitiveType = PrimitiveType.CUBE,
    name: Optional[str] = None,
    location: Optional[Vec3] = None,
    rotation: Optional[Vec3] = None,
    scale: Optional[Vec3] = None,
) -> str:
    """Create a new primitive mesh object in the Blender scene."""
    cmd_params: Dict[str, Any] = {"type": _enum_value(primitive_type)}
    if name:
        cmd_params["name"] = name
    loc = _vec3_arg(location)
    rot = _vec3_arg(rotation)
    scl = _vec3_arg(scale)
    if loc:
        cmd_params["location"] = loc
    if rot:
        cmd_params["rotation"] = rot
    if scl:
        cmd_params["scale"] = scl
    try:
        return _format_blender_result(_send_blender_command("create_object", cmd_params))
    except Exception as exc:
        return _handle_blender_error(exc)


@mcp.tool(
    name="blender_modify_object",
    annotations={
        "title": "Modify Blender Object Properties",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_modify_object(
    name: str,
    location: Optional[Vec3] = None,
    rotation: Optional[Vec3] = None,
    scale: Optional[Vec3] = None,
    visible: Optional[bool] = None,
) -> str:
    """Modify an existing Blender object's transform properties or visibility."""
    cmd_params: Dict[str, Any] = {"name": name}
    loc = _vec3_arg(location)
    rot = _vec3_arg(rotation)
    scl = _vec3_arg(scale)
    if loc:
        cmd_params["location"] = loc
    if rot:
        cmd_params["rotation"] = rot
    if scl:
        cmd_params["scale"] = scl
    if visible is not None:
        cmd_params["visible"] = visible
    try:
        return _format_blender_result(_send_blender_command("modify_object", cmd_params))
    except Exception as exc:
        return _handle_blender_error(exc)


@mcp.tool(
    name="blender_delete_object",
    annotations={
        "title": "Delete Blender Object",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def blender_delete_object(name: str) -> str:
    """Permanently delete an object from the current Blender scene. Destructive."""
    try:
        return _format_blender_result(
            _send_blender_command("delete_object", {"name": name})
        )
    except Exception as exc:
        return _handle_blender_error(exc)


# ===========================================================================
# MCP Tools — Materials & Rendering
# ===========================================================================

@mcp.tool(
    name="blender_set_material",
    annotations={
        "title": "Set / Create Material on Object",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_set_material(
    object_name: str,
    material_name: str,
    color: Optional[List[float]] = None,
) -> str:
    """Create a Principled BSDF material (reusing by name) and assign it to an object."""
    if color is not None:
        if len(color) not in (3, 4):
            return "Error: color must be [R, G, B] or [R, G, B, A]"
        if any(not (0.0 <= c <= 1.0) for c in color):
            return "Error: color channels must be in [0.0, 1.0]"
    cmd_params: Dict[str, Any] = {
        "object_name": object_name,
        "material_name": material_name,
    }
    if color:
        cmd_params["color"] = color
    try:
        return _format_blender_result(_send_blender_command("set_material", cmd_params))
    except Exception as exc:
        return _handle_blender_error(exc)


@mcp.tool(
    name="blender_render_image",
    annotations={
        "title": "Render Current Scene to File",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_render_image(file_path: str) -> str:
    """Trigger a render of the current Blender scene and save the result to disk."""
    try:
        return _format_blender_result(
            _send_blender_command("render_image", {"file_path": file_path})
        )
    except Exception as exc:
        return _handle_blender_error(exc)


# ===========================================================================
# MCP Tools — Advanced / Code Execution
# ===========================================================================

@mcp.tool(
    name="blender_execute_code",
    annotations={
        "title": "Execute Python Code in Blender",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def blender_execute_code(code: str) -> str:
    """Execute arbitrary Python code inside Blender using the bpy module. Destructive."""
    try:
        return _format_blender_result(
            _send_blender_command("execute_blender_code", {"code": code})
        )
    except Exception as exc:
        return _handle_blender_error(exc)


# ===========================================================================
# MCP Tools — PolyHaven Asset Integration
# ===========================================================================

@mcp.tool(
    name="blender_get_polyhaven_categories",
    annotations={
        "title": "List PolyHaven Asset Categories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def blender_get_polyhaven_categories(
    asset_type: PolyHavenAssetType = PolyHavenAssetType.TEXTURES,
) -> str:
    """Fetch the list of available asset categories from the PolyHaven API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{POLYHAVEN_API_BASE}/categories/{_enum_value(asset_type)}"
            resp = await client.get(url)
            resp.raise_for_status()
            categories = resp.json()
        return json.dumps(categories, indent=2)
    except httpx.HTTPStatusError as exc:
        return (
            f"Error: PolyHaven API returned HTTP {exc.response.status_code}: "
            f"{exc.response.text}"
        )
    except httpx.ConnectError:
        return "Error: Cannot reach PolyHaven API. Check your internet connection."
    except Exception as exc:
        return f"Error fetching PolyHaven categories: {type(exc).__name__}: {exc}"


@mcp.tool(
    name="blender_search_polyhaven_assets",
    annotations={
        "title": "Search PolyHaven Assets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def blender_search_polyhaven_assets(
    asset_type: PolyHavenAssetType = PolyHavenAssetType.TEXTURES,
    categories: Optional[List[str]] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """Search the PolyHaven asset library by type and optional category filters."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            url = f"{POLYHAVEN_API_BASE}/assets"
            query: Dict[str, Any] = {"type": _enum_value(asset_type)}
            if categories:
                query["categories"] = ",".join(categories)
            resp = await client.get(url, params=query)
            resp.raise_for_status()
            all_assets: Dict[str, Any] = resp.json()

        items = list(all_assets.items())
        total = len(items)
        page = items[offset: offset + limit]
        result = {
            "total": total,
            "count": len(page),
            "offset": offset,
            "has_more": total > offset + len(page),
            "next_offset": offset + len(page)
            if total > offset + len(page)
            else None,
            "items": [{"id": k, **v} for k, v in page],
        }
        return json.dumps(result, indent=2)
    except httpx.HTTPStatusError as exc:
        return f"Error: PolyHaven API returned HTTP {exc.response.status_code}"
    except httpx.ConnectError:
        return "Error: Cannot reach PolyHaven API. Check your internet connection."
    except Exception as exc:
        return f"Error searching PolyHaven: {type(exc).__name__}: {exc}"


@mcp.tool(
    name="blender_download_polyhaven_asset",
    annotations={
        "title": "Download PolyHaven Asset into Blender",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def blender_download_polyhaven_asset(
    asset_id: str,
    asset_type: PolyHavenAssetType = PolyHavenAssetType.TEXTURES,
    resolution: str = "1k",
    file_format: str = "jpg",
) -> str:
    """Download a PolyHaven asset and import it into the active Blender scene."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            files_url = f"{POLYHAVEN_API_BASE}/files/{asset_id}"
            resp = await client.get(files_url)
            resp.raise_for_status()
            files_data: Dict[str, Any] = resp.json()
    except httpx.ConnectError:
        return "Error: Cannot reach PolyHaven API. Check your internet connection."
    except httpx.HTTPStatusError as exc:
        return (
            f"Error: PolyHaven API returned HTTP {exc.response.status_code} "
            f"for asset '{asset_id}'"
        )
    except Exception as exc:
        return f"Error fetching PolyHaven asset info: {type(exc).__name__}: {exc}"

    try:
        cmd_params: Dict[str, Any] = {
            "asset_id": asset_id,
            "asset_type": _enum_value(asset_type),
            "resolution": resolution,
            "file_format": file_format,
            "files_data": files_data,
        }
        return _format_blender_result(
            _send_blender_command("download_polyhaven_asset", cmd_params)
        )
    except Exception as exc:
        return _handle_blender_error(exc)


@mcp.tool(
    name="blender_set_texture",
    annotations={
        "title": "Apply Downloaded PolyHaven Texture to Object",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_set_texture(object_name: str, texture_id: str) -> str:
    """Apply a previously downloaded PolyHaven texture to a Blender object."""
    try:
        return _format_blender_result(
            _send_blender_command(
                "set_texture",
                {"object_name": object_name, "texture_id": texture_id},
            )
        )
    except Exception as exc:
        return _handle_blender_error(exc)


# ===========================================================================
# MCP Tools — LLM / AI Integration (provider-agnostic)
# ===========================================================================

@mcp.tool(
    name="blender_ai_prompt",
    annotations={
        "title": "Send Natural Language Prompt to LLM",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def blender_ai_prompt(
    prompt: str,
    system_prompt: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """
    Send a natural-language prompt to the configured LLM backend.

    Works with any supported provider: OpenAI-compatible endpoints (OpenAI,
    Azure AI Foundry, LM Studio, llama.cpp, vLLM, ...) and Ollama. Use
    blender_set_llm_provider first to choose the backend, or override the
    provider/base_url/model per call.
    """
    return await _query_llm(
        prompt,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
    )


@mcp.tool(
    name="blender_get_llm_provider",
    annotations={
        "title": "Get Active LLM Provider",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_get_llm_provider() -> str:
    """Return the active LLM provider configuration (API key masked)."""
    try:
        return json.dumps(_redact_state(), indent=2)
    except Exception as exc:
        return _handle_provider_error(exc)


@mcp.tool(
    name="blender_set_llm_provider",
    annotations={
        "title": "Switch LLM Provider",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_set_llm_provider(
    provider: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Switch/configure the active LLM provider at runtime.

    Examples:
      - Ollama:    provider="ollama", base_url="http://localhost:11434", model="llama3.2"
      - LM Studio: provider="lmstudio", base_url="http://localhost:1234/v1", model="local-model"
      - llama.cpp: provider="llamacpp", base_url="http://localhost:8080/v1"
      - OpenAI:    provider="openai", api_key="sk-...", model="gpt-4o-mini"
      - Generic:   provider="openai_compat", base_url="<any>", api_key="..."
      - Azure:     provider="azure", api_key="...",
                   extra={"resource": "r", "deployment": "d", "api_version": "..."}
    """
    try:
        _apply_provider_config(provider, base_url, api_key, model, extra)
        return json.dumps(_redact_state(), indent=2)
    except Exception as exc:
        return _handle_provider_error(exc)


@mcp.tool(
    name="blender_list_llm_models",
    annotations={
        "title": "List Available LLM Models",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def blender_list_llm_models(
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    """
    List models available from a provider (Ollama /api/tags, OpenAI-compatible
    /models). Defaults to the active provider.
    """
    provider_key = provider or _llm_state["provider"]
    try:
        models = await llm_backend.list_models(
            provider=provider_key,
            base_url=base_url or _llm_state["base_url"],
            api_key=api_key if api_key is not None else _llm_state["api_key"],
            extra=_llm_state.get("extra", {}),
        )
        return json.dumps(
            {
                "provider": provider_key,
                "base_url": base_url or _llm_state["base_url"],
                "models": models,
            },
            indent=2,
        )
    except Exception as exc:
        return _handle_provider_error(exc)


# Backward-compatible aliases ------------------------------------------------

@mcp.tool(
    name="blender_get_ollama_models",
    annotations={
        "title": "List Ollama Models",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def blender_get_ollama_models() -> str:
    """List models available from the active Ollama endpoint (alias for blender_list_llm_models)."""
    return await blender_list_llm_models(provider="ollama")


@mcp.tool(
    name="blender_set_ollama_model",
    annotations={
        "title": "Set Ollama Model",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_set_ollama_model(model_name: str) -> str:
    """Set the Ollama model used by the LLM backend."""
    return await blender_set_llm_provider(provider="ollama", model=model_name)


@mcp.tool(
    name="blender_set_ollama_url",
    annotations={
        "title": "Set Ollama Server URL",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def blender_set_ollama_url(url: str) -> str:
    """Update the Ollama server base URL used by the LLM backend."""
    return await blender_set_llm_provider(provider="ollama", base_url=url)


# ===========================================================================
# MCP Prompts (prompts/list + prompts/get)
# ===========================================================================

from .prompts import register_prompts  # noqa: E402

register_prompts(mcp)


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    """CLI entry point registered as `blender-mcp` in pyproject.toml."""
    global BLENDER_HOST, BLENDER_PORT, _llm_state
    parser = argparse.ArgumentParser(
        prog="blender-mcp",
        description="blender-open-mcp: MCP server for controlling Blender3D "
        "with provider-agnostic local/remote AI backends.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="FastMCP server host")
    parser.add_argument("--port", type=int, default=8000, help="FastMCP server port")
    parser.add_argument(
        "--blender-host", default=BLENDER_HOST, help="Blender add-on TCP host"
    )
    parser.add_argument(
        "--blender-port", type=int, default=BLENDER_PORT, help="Blender add-on TCP port"
    )
    parser.add_argument(
        "--transport",
        choices=["streamable_http", "http", "stdio"],
        default="streamable_http",
        help="MCP transport (streamable_http/http or stdio)",
    )
    # LLM provider configuration (startup defaults; can be switched at runtime)
    parser.add_argument(
        "--llm-provider", default=None,
        help="LLM provider: openai, openai_compat, ollama, lmstudio, llamacpp, azure",
    )
    parser.add_argument("--llm-base-url", default=None,
                        help="LLM provider base URL")
    parser.add_argument("--llm-api-key", default=None,
                        help="LLM provider API key (optional for local servers)")
    parser.add_argument("--llm-model", default=None,
                        help="LLM model name (Azure: deployment name)")
    parser.add_argument("--llm-extra", default=None,
                        help="JSON dict of provider-specific extras, e.g. "
                        '\'{"resource":"r","deployment":"d"}\' for Azure')
    args = parser.parse_args()

    extra_cfg: Optional[Dict[str, Any]] = None
    if args.llm_extra:
        try:
            extra_cfg = json.loads(args.llm_extra)
            if not isinstance(extra_cfg, dict):
                raise ValueError("must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"error: --llm-extra {exc}", file=sys.stderr)
            sys.exit(2)

    BLENDER_HOST = args.blender_host
    BLENDER_PORT = args.blender_port

    try:
        if (
            args.llm_provider
            or args.llm_base_url
            or args.llm_model
            or args.llm_api_key
            or extra_cfg
        ):
            _apply_provider_config(
                provider=args.llm_provider,
                base_url=args.llm_base_url,
                api_key=args.llm_api_key,
                model=args.llm_model,
                extra=extra_cfg,
            )
        # Normalize provider default from env.
        _llm_state["provider"] = resolve_provider(_llm_state["provider"])
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    logger.info("Starting blender-open-mcp server")
    logger.info("  FastMCP transport : %s", args.transport)
    if args.transport in ("streamable_http", "http"):
        logger.info("  FastMCP endpoint  : http://%s:%d", args.host, args.port)
    logger.info("  Blender add-on    : %s:%d", args.blender_host, args.blender_port)
    logger.info("  LLM provider      : %s", _llm_state["provider"])
    logger.info("  LLM base URL      : %s", _llm_state["base_url"])
    logger.info("  LLM model         : %s", _llm_state["model"])
    logger.info("  LLM extra         : %s", _llm_state.get("extra", {}))

    # fastmcp 4 expects "streamable-http"/"http"/"stdio"; normalize legacy alias.
    transport = {
        "streamable_http": "streamable-http",
        "http": "streamable-http",
    }.get(args.transport, args.transport)
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
