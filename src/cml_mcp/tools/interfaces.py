# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""
Interface management tools for CML MCP server.
"""

import logging

import httpx
from fastmcp import Context
from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml.simple_webserver.schemas.interfaces import InterfaceCreate
from cml_mcp.cml_client import CMLClient
from cml_mcp.tools.dependencies import get_cml_client_dep, resolve_lab_id, resolve_node_id, run_with_heartbeat
from cml_mcp.tools.nodes import stop_node, wipe_node
from cml_mcp.types import SimplifiedInterfaceResponse

logger = logging.getLogger("cml-mcp.tools.interfaces")


async def add_interface(lid: UUID4Type, intf: InterfaceCreate, client: CMLClient) -> SimplifiedInterfaceResponse:
    """
    Add an interface to a CML node. The CML API returns a list of all interfaces after creation;
    we return the last one (the newly added slot).
    """
    resp = await client.post(f"/labs/{lid}/interfaces", data=intf.model_dump(mode="json", exclude_none=True))
    # CML returns the full interface list for the node after adding; grab the last (newly created) entry
    if isinstance(resp, list):
        if not resp:
            raise ToolError("CML returned an empty interface list after creation.")
        new_iface = resp[-1]
    else:
        new_iface = resp
    return SimplifiedInterfaceResponse(**new_iface).model_dump(exclude_unset=True)


def register_tools(mcp):
    """Register all interface-related tools with the FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Add an Interface to a CML Node",
            "readOnlyHint": False,
            "destructiveHint": False,
        },
    )
    async def add_interface_to_node(
        lab_name: str,
        node_label: str,
        ctx: Context,
        slot: int | None = None,
        mac_address: str | None = None,
    ) -> SimplifiedInterfaceResponse:
        """
        Add interface to node by lab name and node label. Returns interface with id, node, slot, type, and MAC address.
        If the node has been started before, it is automatically stopped and wiped first (physical config is locked after boot).
        Wiping erases runtime state — reconfigure the node and restart it after adding the interface.
        slot: interface slot number 0-128 (optional, CML picks next available if omitted).
        mac_address: "00:11:22:33:44:55" format (optional).
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            nid = await resolve_node_id(lid, node_label, lab_name, client)
            intf = InterfaceCreate(node=nid, slot=slot, mac_address=mac_address)
            try:
                return await add_interface(lid, intf, client)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400 and "locked" in e.response.text.lower():
                    logger.info(f"Node '{node_label}' config is locked — auto stopping and wiping before adding interface")
                    await run_with_heartbeat(stop_node(lid, nid, client), ctx, f"Stopping '{node_label}' to unlock physical config...")
                    await run_with_heartbeat(wipe_node(lid, nid, client), ctx, f"Wiping '{node_label}' to unlock physical config...")
                    return await add_interface(lid, intf, client)
                raise
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error adding interface to node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={
            "title": "Get Interfaces for a CML Node",
            "readOnlyHint": True,
        },
    )
    async def get_interfaces_for_node(
        lab_name: str,
        node_label: str,
    ) -> list[SimplifiedInterfaceResponse]:
        """
        Get node interfaces by lab name and node label. Returns list with id, node, label, slot, type, MAC address, and IP config.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            nid = await resolve_node_id(lid, node_label, lab_name, client)
            resp = await client.get(f"/labs/{lid}/nodes/{nid}/interfaces", params={"data": True, "operational": False})
            return [SimplifiedInterfaceResponse(**iface).model_dump(exclude_unset=True) for iface in resp]
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error getting interfaces for node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)
