# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""
Node management tools for CML MCP server.
"""

import asyncio
import json
import logging
import random

import httpx
from fastmcp import Context
from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml.simple_webserver.schemas.nodes import Node, NodeConfigurationContent, NodeCreate
from cml_mcp.cml_client import CMLClient
from cml_mcp.tools.dependencies import get_cml_client_dep, resolve_lab_id, resolve_node_id

logger = logging.getLogger("cml-mcp.tools.nodes")


async def stop_node(lid: UUID4Type, nid: UUID4Type, client: CMLClient) -> None:
    """
    Stop a CML node by its lab ID and node ID.

    Args:
        lid (UUID4Type): The lab ID.
        nid (UUID4Type): The node ID.
        client (CMLClient): The CML client instance.
    """
    await client.put(f"/labs/{lid}/nodes/{nid}/state/stop")


async def wipe_node(lid: UUID4Type, nid: UUID4Type, client: CMLClient) -> None:
    """
    Wipe a CML node by its lab ID and node ID.

    Args:
        lid (UUID4Type): The lab ID.
        nid (UUID4Type): The node ID.
        client (CMLClient): The CML client instance.
    """
    await client.put(f"/labs/{lid}/nodes/{nid}/wipe_disks")


async def _run_with_heartbeat(coro, ctx: Context, message: str, interval: int = 8) -> None:
    """
    Run a coroutine while sending periodic MCP progress notifications.
    Keeps the SSE connection alive during long-running CML API calls.
    """
    task = asyncio.create_task(coro)
    elapsed = 0
    while not task.done():
        try:
            await ctx.report_progress(elapsed, None, message)
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            break
        except asyncio.TimeoutError:
            elapsed += interval
    if not task.done():
        await task


_PLACEMENT_MIN = 100
_PLACEMENT_MAX = 2000
_PLACEMENT_CLEARANCE = 200
_PLACEMENT_RETRIES = 20


def _find_free_position(existing_nodes: list[dict]) -> tuple[int, int]:
    """Pick a random canvas position that doesn't overlap any existing node."""
    occupied = [(n.get("x", 0), n.get("y", 0)) for n in existing_nodes]
    for _ in range(_PLACEMENT_RETRIES):
        x = random.randint(_PLACEMENT_MIN, _PLACEMENT_MAX)
        y = random.randint(_PLACEMENT_MIN, _PLACEMENT_MAX)
        if all(abs(x - ox) >= _PLACEMENT_CLEARANCE or abs(y - oy) >= _PLACEMENT_CLEARANCE for ox, oy in occupied):
            return x, y
    # Fallback: extend the canvas area if all retries exhausted
    x = random.randint(_PLACEMENT_MAX, _PLACEMENT_MAX * 2)
    y = random.randint(_PLACEMENT_MAX, _PLACEMENT_MAX * 2)
    return x, y


def register_tools(mcp):  # noqa: C901
    """Register all node-related tools with the FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Get All Nodes for a CML Lab",
            "readOnlyHint": True,
        },
    )
    async def get_nodes_for_cml_lab(lab_name: str) -> list[Node]:
        """
        Get lab nodes by lab name. Returns list with id, label, node_definition, x, y, state, interfaces, and operational data (CPU/RAM/serial).
        Resolves the lab name to its UUID automatically.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            resp = await client.get(f"/labs/{lid}/nodes", params={"data": True, "operational": True, "exclude_configurations": True})
            rnodes = []
            for node in list(resp):
                # XXX: Fixup known issues with bad data coming from
                # certain node types.
                if node.get("operational") is not None:
                    if node["operational"].get("vnc_key") == "":
                        node["operational"]["vnc_key"] = None
                    if node["operational"].get("image_definition") == "":
                        node["operational"]["image_definition"] = None
                    if node["operational"].get("serial_consoles") is None:
                        node["operational"]["serial_consoles"] = []
                rnodes.append(Node(**node).model_dump(exclude_unset=True))
            return rnodes
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error getting nodes for CML lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={
            "title": "Add a Node to a CML Lab",
            "readOnlyHint": False,
            "destructiveHint": False,
        },
    )
    async def add_node_to_cml_lab(
        lab_name: str,
        node_definition: str,
        label: str,
        image_definition: str | None = None,
        ram: int | None = None,
        cpus: int | None = None,
        cpu_limit: int | None = None,
        data_volume: int | None = None,
        boot_disk_size: int | None = None,
        tags: list[str] | None = None,
        configuration: str | None = None,
    ) -> UUID4Type:
        """
        Add node to lab by lab name. Returns node UUID. Auto-creates default interfaces per node definition.
        Canvas position is chosen automatically — existing nodes are checked to avoid overlap.
        node_definition: node type ID, e.g. "alpine", "iosv", "iol-xe" — use get_cml_node_definitions to list available values.
        label: display name for the node (1-128 chars).
        ram: memory in MB. cpus: vCPU count. cpu_limit: CPU limit %. data_volume/boot_disk_size: disk sizes in GB.
        configuration: startup config as a plain string of device commands.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            existing = await client.get(
                f"/labs/{lid}/nodes",
                params={"data": True, "operational": False, "exclude_configurations": True},
            )
            x, y = _find_free_position(list(existing))
            logger.debug(f"Auto-placed node '{label}' at ({x}, {y}) in lab '{lab_name}'")
            node_kwargs: dict = {"node_definition": node_definition, "label": label, "x": x, "y": y}
            for k, v in {
                "image_definition": image_definition,
                "ram": ram,
                "cpus": cpus,
                "cpu_limit": cpu_limit,
                "data_volume": data_volume,
                "boot_disk_size": boot_disk_size,
                "tags": tags,
                "configuration": configuration,
            }.items():
                if v is not None:
                    node_kwargs[k] = v
            node = NodeCreate(**node_kwargs)
            resp = await client.post(
                f"/labs/{lid}/nodes", params={"populate_interfaces": True}, data=node.model_dump(mode="json", exclude_defaults=True)
            )
            return UUID4Type(resp["id"])
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error adding CML node to lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Configure a CML Node", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def configure_cml_node(
        lid: UUID4Type,
        nid: UUID4Type,
        config: NodeConfigurationContent,
    ) -> bool:
        """
        Set node startup config by lab and node UUID. config is a plain string of device commands.
        Node must be in CREATED state (new or wiped). More efficient than starting node and sending CLI.
        """
        client = get_cml_client_dep()
        payload = {"configuration": str(config)}
        try:
            await client.patch(f"/labs/{lid}/nodes/{nid}", data=payload)
            return True
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error configuring CML node {nid} in lab {lid}: {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Stop a CML Node", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def stop_cml_node(lab_name: str, node_label: str, ctx: Context) -> bool:
        """
        Stop node by lab name and node label. Powers down the node.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            nid = await resolve_node_id(lid, node_label, lab_name, client)
            await _run_with_heartbeat(
                stop_node(lid, nid, client), ctx, f"Stopping node '{node_label}'..."
            )
            return True
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error stopping CML node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={
            "title": "Start a CML Node",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def start_cml_node(
        lab_name: str,
        node_label: str,
        ctx: Context,
        wait_for_convergence: bool = False,
    ) -> bool:
        """
        Start node by lab name and node label. Set wait_for_convergence=true to wait until node reaches stable state.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            nid = await resolve_node_id(lid, node_label, lab_name, client)
            await _run_with_heartbeat(
                client.put(f"/labs/{lid}/nodes/{nid}/state/start"), ctx, f"Starting node '{node_label}'..."
            )
            if wait_for_convergence:
                elapsed = 0
                while True:
                    converged = await client.get(f"/labs/{lid}/nodes/{nid}/check_if_converged")
                    if converged:
                        break
                    try:
                        await ctx.report_progress(elapsed, None, f"Waiting for '{node_label}' to converge...")
                    except Exception:
                        pass
                    await asyncio.sleep(3)
                    elapsed += 3
            return True
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error starting CML node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Wipe a CML Node", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    async def wipe_cml_node(lab_name: str, node_label: str, ctx: Context) -> bool:
        """
        Wipe node by lab name and node label. Erases all node data. Node must be stopped first.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            nid = await resolve_node_id(lid, node_label, lab_name, client)
            await _run_with_heartbeat(wipe_node(lid, nid, client), ctx, f"Wiping node '{node_label}'...")
            return True
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error wiping CML node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Delete a node from a CML lab.", "readOnlyHint": False, "destructiveHint": True},
    )
    async def delete_cml_node(lab_name: str, node_label: str, ctx: Context) -> bool:
        """
        Delete node by lab name and node label. Auto-stops and wipes before deleting.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            nid = await resolve_node_id(lid, node_label, lab_name, client)
            await _run_with_heartbeat(stop_node(lid, nid, client), ctx, f"Stopping node '{node_label}' before deletion...")
            await _run_with_heartbeat(wipe_node(lid, nid, client), ctx, f"Wiping node '{node_label}'...")
            await client.delete(f"/labs/{lid}/nodes/{nid}")
            return True
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error deleting CML node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)
