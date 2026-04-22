# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""
Link management tools for CML MCP server.
"""

import logging

import httpx
from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml.simple_webserver.schemas.interfaces import InterfaceCreate
from cml_mcp.cml.simple_webserver.schemas.links import LinkConditionConfiguration, LinkCreate, LinkResponse
from cml_mcp.tools.dependencies import get_cml_client_dep, parse_str_arg, resolve_lab_id
from cml_mcp.types import SimplifiedInterfaceResponse

logger = logging.getLogger("cml-mcp.tools.links")


def register_tools(mcp):
    """Register all link-related tools with the FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Connect Two Nodes by Label in a CML Lab",
            "readOnlyHint": False,
            "destructiveHint": False,
        },
    )
    async def connect_nodes_by_label(
        lab_name: str,
        node_a_label: str,
        node_b_label: str,
    ) -> UUID4Type:
        """
        Connect two nodes by their labels (names) within a lab identified by name. Returns link UUID.
        Resolves lab name and node labels to UUIDs, finds the first free (unconnected) interface on
        each node, creates a new interface slot if none are free, then creates the link.
        Raises an error if the lab name or either node label is not found or is ambiguous.
        """
        client = get_cml_client_dep()
        logger.info(f"Connecting nodes '{node_a_label}' and '{node_b_label}' in lab '{lab_name}'")
        try:
            # Step 1: Resolve lab name to UUID
            lid = await resolve_lab_id(lab_name, client)

            # Step 2: Fetch all nodes and resolve both labels to unique node UUIDs
            logger.info(f"Fetching nodes for lab {lid}")
            nodes_resp = await client.get(
                f"/labs/{lid}/nodes",
                params={"data": True, "operational": False, "exclude_configurations": True},
            )
            nodes = list(nodes_resp)
            logger.debug(f"Found {len(nodes)} node(s) in lab '{lab_name}'")

            def find_node_id(label: str) -> UUID4Type:
                matches = [n for n in nodes if n.get("label") == label]
                if not matches:
                    logger.error(f"No node with label '{label}' found in lab '{lab_name}'")
                    raise ToolError(f"No node found with label '{label}' in lab '{lab_name}'.")
                if len(matches) > 1:
                    logger.error(f"Ambiguous node label '{label}' in lab '{lab_name}': {len(matches)} matches")
                    raise ToolError(f"Multiple nodes found with label '{label}' in lab '{lab_name}'. Labels must be unique.")
                nid = UUID4Type(matches[0]["id"])
                logger.debug(f"Resolved node label '{label}' → {nid}")
                return nid

            nid_a = find_node_id(node_a_label)
            nid_b = find_node_id(node_b_label)
            logger.info(f"Resolved nodes: '{node_a_label}' → {nid_a}, '{node_b_label}' → {nid_b}")

            # Step 3: Get interfaces for each node and find the first free one.
            # If no free interface exists, add a new slot (CML auto-assigns the next slot).
            async def get_free_interface(nid: UUID4Type, node_label: str) -> UUID4Type:
                logger.info(f"Fetching interfaces for node '{node_label}' ({nid})")
                ifaces_resp = await client.get(
                    f"/labs/{lid}/nodes/{nid}/interfaces",
                    params={"data": True, "operational": False},
                )
                ifaces = [SimplifiedInterfaceResponse(**iface) for iface in ifaces_resp]
                logger.debug(f"Node '{node_label}' ({nid}): {len(ifaces)} interface(s) total")

                # Skip loopbacks (cannot form links) and management interfaces (not for data traffic)
                def _is_connectable(iface: SimplifiedInterfaceResponse) -> bool:
                    if iface.type == "loopback":
                        return False
                    if iface.label and iface.label.lower().startswith(("mgmt", "management")):
                        return False
                    return True

                connectable = [iface for iface in ifaces if _is_connectable(iface)]
                skipped = len(ifaces) - len(connectable)
                if skipped:
                    skipped_labels = [i.label for i in ifaces if not _is_connectable(i)]
                    logger.debug(f"Node '{node_label}' ({nid}): skipped {skipped} non-connectable interface(s): {skipped_labels}")

                free = [iface for iface in connectable if not iface.is_connected]
                logger.debug(f"Node '{node_label}' ({nid}): {len(free)} free connectable interface(s)")
                if free:
                    chosen = free[0]
                    logger.info(f"Node '{node_label}' ({nid}): selected free interface '{chosen.label}' ({chosen.id})")
                    return chosen.id
                # No free connectable interface — add a new one (slot=None lets CML pick the next slot)
                logger.info(f"Node '{node_label}' ({nid}): no free connectable interfaces, creating a new slot")
                new_iface_resp = await client.post(
                    f"/labs/{lid}/interfaces",
                    data=InterfaceCreate(node=nid).model_dump(mode="json", exclude_none=True),
                )
                new_iface = SimplifiedInterfaceResponse(**new_iface_resp)
                logger.info(f"Node '{node_label}' ({nid}): created new interface '{new_iface.label}' ({new_iface.id})")
                return new_iface.id

            src_int = await get_free_interface(nid_a, node_a_label)
            dst_int = await get_free_interface(nid_b, node_b_label)

            # Step 4: Create the link between the two free interfaces
            logger.info(f"Creating link between interface {src_int} ('{node_a_label}') and {dst_int} ('{node_b_label}')")
            link_data = LinkCreate(src_int=src_int, dst_int=dst_int)
            resp = await client.post(f"/labs/{lid}/links", data=link_data.model_dump(mode="json"))
            link_id = UUID4Type(resp["id"])
            logger.info(f"Link {link_id} created between '{node_a_label}' and '{node_b_label}' in lab '{lab_name}'")
            return link_id

        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error connecting '{node_a_label}' to '{node_b_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={
            "title": "Get All Links for a CML Lab",
            "readOnlyHint": True,
        },
    )
    async def get_all_links_for_lab(lid: UUID4Type) -> list[LinkResponse]:
        """
        Get lab links by UUID. Returns list with id, label, interface_a, interface_b, node_a, node_b, state, and capture_key.
        """
        client = get_cml_client_dep()
        logger.info(f"Fetching all links for lab {lid}")
        try:
            resp = await client.get(f"/labs/{lid}/links", params={"data": True})
            links = [LinkResponse(**link).model_dump(exclude_unset=True) for link in resp]
            logger.info(f"Retrieved {len(links)} link(s) for lab {lid}")
            return links
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error getting links for lab {lid}: {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Apply Link Conditioning", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def apply_link_conditioning(
        lid: UUID4Type,
        link_id: UUID4Type,
        condition: LinkConditionConfiguration | dict,
    ) -> bool:
        """
        Configure link network conditions by lab and link UUID.
        Omit fields to leave existing values unchanged.
        Fields (all optional): bandwidth (kbps, 0-10M), latency (ms, 0-10K), loss (%, 0-100), jitter (ms, 0-10K),
        duplicate (%, 0-100), corrupt_prob (%, 0-100), gap (ms), limit (ms), reorder_prob (%, 0-100),
        delay_corr/loss_corr/duplicate_corr/reorder_corr/corrupt_corr (%, 0-100), enabled (bool).
        """
        client = get_cml_client_dep()
        logger.info(f"Applying link conditioning to link {link_id} in lab {lid}")
        try:
            # XXX The dict/str handling is a workaround for some LLMs that pass a JSON string
            # representation of the argument object.
            if isinstance(condition, str):
                try:
                    condition = LinkConditionConfiguration(**parse_str_arg(condition))
                except Exception as parse_err:
                    raise ToolError(f"condition must be an object, got invalid string: {parse_err}")
            elif isinstance(condition, dict):
                condition = LinkConditionConfiguration(**condition)
            logger.debug(f"Link conditioning payload for {link_id}: {condition.model_dump(exclude_none=True)}")
            await client.patch(f"/labs/{lid}/links/{link_id}/condition", data=condition.model_dump(mode="json", exclude_none=True))
            logger.info(f"Link conditioning applied to link {link_id} in lab {lid}")
            return True
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error conditioning link {link_id} in lab {lid}: {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={
            "title": "Start a CML Link",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def start_cml_link(lid: UUID4Type, link_id: UUID4Type) -> bool:
        """
        Start link by lab and link UUID. Enables connectivity.
        """
        client = get_cml_client_dep()
        logger.info(f"Starting link {link_id} in lab {lid}")
        try:
            await client.put(f"/labs/{lid}/links/{link_id}/state/start")
            logger.info(f"Link {link_id} started in lab {lid}")
            return True
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error starting CML link {link_id} in lab {lid}: {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={
            "title": "Stop a CML Link",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def stop_cml_link(lid: UUID4Type, link_id: UUID4Type) -> bool:
        """
        Stop link by lab and link UUID. Disables connectivity.
        """
        client = get_cml_client_dep()
        logger.info(f"Stopping link {link_id} in lab {lid}")
        try:
            await client.put(f"/labs/{lid}/links/{link_id}/state/stop")
            logger.info(f"Link {link_id} stopped in lab {lid}")
            return True
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error stopping CML link {link_id} in lab {lid}: {str(e)}", exc_info=True)
            raise ToolError(e)
