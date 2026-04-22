# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""
Packet capture (PCAP) tools for CML MCP server.
"""

import base64
import json
import logging

import httpx
from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml.simple_webserver.schemas.pcap import PCAPItem, PCAPStart, PCAPStatusResponse
from cml_mcp.cml_client import CMLClient
from cml_mcp.tools.dependencies import get_cml_client_dep, parse_str_arg, resolve_lab_id, resolve_link_id

logger = logging.getLogger("cml-mcp.tools.pcap")


async def get_capture_key(lid: UUID4Type, link_id: UUID4Type, client: CMLClient) -> str:
    """
    Get the capture key for the link.
    """
    key = await client.get(f"/labs/{lid}/links/{link_id}/capture/key")
    if not key:
        raise ToolError("No packet capture found for the specified link.")
    return key


def register_tools(mcp):
    """Register all packet capture tools with the FastMCP server."""

    @mcp.tool(
        annotations={"title": "Start a Packet Capture on a Link", "readOnlyHint": False, "destructiveHint": False},
    )
    async def start_packet_capture(
        lab_name: str,
        node_a_label: str,
        node_b_label: str,
        pcap: PCAPStart | dict,
    ) -> bool:
        """
        Start a packet capture on the link between two nodes by lab name and node labels.
        At least one of maxtime or maxpackets is required in pcap. Returns true if successful.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            link_id = await resolve_link_id(lid, node_a_label, node_b_label, lab_name, client)
            # XXX The dict/str handling is a workaround for some LLMs that pass a JSON string
            # representation of the argument object.
            if isinstance(pcap, str):
                try:
                    pcap = PCAPStart(**parse_str_arg(pcap))
                except Exception as parse_err:
                    raise ToolError(f"pcap must be an object, got invalid string: {parse_err}")
            elif isinstance(pcap, dict):
                pcap = PCAPStart(**pcap)
            await client.put(f"/labs/{lid}/links/{link_id}/capture/start", data=pcap.model_dump(mode="json", exclude_none=True))
            return True
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error starting packet capture on link '{node_a_label}' ↔ '{node_b_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Stop a Packet Capture on a Link", "readOnlyHint": False, "destructiveHint": False},
    )
    async def stop_packet_capture(lab_name: str, node_a_label: str, node_b_label: str) -> bool:
        """
        Stop a packet capture on the link between two nodes by lab name and node labels. Returns true if successful.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            link_id = await resolve_link_id(lid, node_a_label, node_b_label, lab_name, client)
            await client.put(f"/labs/{lid}/links/{link_id}/capture/stop")
            return True
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error stopping packet capture on link '{node_a_label}' ↔ '{node_b_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Check Packet Capture Status on a Link", "readOnlyHint": True},
    )
    async def check_packet_capture_status(lab_name: str, node_a_label: str, node_b_label: str) -> PCAPStatusResponse:
        """
        Check if a packet capture is active on the link between two nodes by lab name and node labels.
        Returns capture config and number of packets captured so far.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            link_id = await resolve_link_id(lid, node_a_label, node_b_label, lab_name, client)
            status = await client.get(f"/labs/{lid}/links/{link_id}/capture/status")
            return PCAPStatusResponse(**status).model_dump(exclude_unset=True)
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error checking packet capture status on link '{node_a_label}' ↔ '{node_b_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Get packet capture overview", "readOnlyHint": True},
    )
    async def get_captured_packet_overview(lab_name: str, node_a_label: str, node_b_label: str) -> list[PCAPItem]:
        """
        Get a brief summary of each packet captured on the link between two nodes by lab name and node labels.
        Returns list of PCAPItem objects.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            link_id = await resolve_link_id(lid, node_a_label, node_b_label, lab_name, client)
            key = await get_capture_key(lid, link_id, client)
            packets = await client.get(f"/pcap/{key}/packets")
            return [PCAPItem(**packet).model_dump(exclude_unset=True) for packet in packets]
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error getting packet capture overview on link '{node_a_label}' ↔ '{node_b_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

    @mcp.tool(
        annotations={"title": "Get Full Packets from a Packet Capture", "readOnlyHint": True},
    )
    async def get_packet_capture_data(lab_name: str, node_a_label: str, node_b_label: str) -> str:
        """
        Download complete packet capture on the link between two nodes by lab name and node labels.
        Returns base64-encoded PCAP file. Decode and save as .pcap file for use with Wireshark, tcpdump, or other packet analysis tools.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            link_id = await resolve_link_id(lid, node_a_label, node_b_label, lab_name, client)
            key = await get_capture_key(lid, link_id, client)
            pcap_data = await client.get(f"/pcap/{key}", is_binary=True)
            encoded_pcap = base64.b64encode(pcap_data).decode("utf-8")
            return encoded_pcap
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error getting packet capture data from link '{node_a_label}' ↔ '{node_b_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)
