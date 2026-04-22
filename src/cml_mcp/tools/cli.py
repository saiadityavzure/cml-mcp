# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""
CLI command and console log tools for CML MCP server.
"""

import asyncio
import logging
import os
import re
import tempfile

import httpx
from fastmcp.exceptions import ToolError
from virl2_client.models.cl_pyats import ClPyats, PyatsNotInstalled

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml_client import CMLClient
from cml_mcp.tools.dependencies import _pyats_auth_pass, _pyats_password, _pyats_username, get_cml_client_dep, resolve_lab_id, resolve_node_id
from cml_mcp.types import ConsoleLogOutput

logger = logging.getLogger("cml-mcp.tools.cli")


def _send_cli_command_sync(
    client: CMLClient,
    lid: UUID4Type,
    label: str,
    commands: str,
    config_command: bool,
) -> str:
    """
    Synchronous helper for send_cli_command to isolate blocking operations in a thread.
    This prevents os.chdir() race conditions and event loop blocking.
    """
    cwd = os.getcwd()  # Save the current working directory
    try:
        os.chdir(tempfile.gettempdir())  # Change to a writable directory (required by pyATS/ClPyats)
        lab = client.vclient.join_existing_lab(str(lid))  # Join the existing lab using the provided lab ID
        try:
            pylab = ClPyats(lab)  # Create a ClPyats object for interacting with the lab
            pylab.sync_testbed(client.vclient.username, client.vclient.password)  # Sync the testbed with CML credentials

            # Set the credentials for all devices other than the Terminal Server
            # For HTTP transport: use contextvars (request-scoped, prevents race conditions)
            # For stdio transport: fall back to environment variables
            for device in pylab._testbed.devices.values():
                if device.name != "terminal_server":
                    device.credentials.default.username = _pyats_username.get() or os.getenv("PYATS_USERNAME", "cisco")
                    device.credentials.default.password = _pyats_password.get() or os.getenv("PYATS_PASSWORD", "cisco")
                    device.credentials.enable.password = (
                        _pyats_auth_pass.get() or os.getenv("PYATS_AUTH_PASS") or device.credentials.default.password
                    )

        except PyatsNotInstalled:
            raise ImportError(
                "PyATS and Genie are required to send commands to running devices.  See the documentation on how to install them."
            )
        if config_command:
            # Send the command as a configuration command
            results = pylab.run_config_command(str(label), commands)
        else:
            # Send the command as an exec/operational command
            results = pylab.run_command(str(label), commands)

        # Genie may return dict output where the key is the command and the value is its output.
        if isinstance(results, dict):
            output = ""
            for cmd, cmd_output in results.items():
                output += f"Command: {cmd}\nOutput:\n{cmd_output}\n"
        else:
            output = str(results)

        return output
    finally:
        os.chdir(cwd)  # Restore the original working directory


def register_tools(mcp):
    """Register all CLI and console tools with the FastMCP server."""

    @mcp.tool(
        annotations={"title": "Get Console Logs for a CML Node", "readOnlyHint": True},
    )
    async def get_console_log(
        lab_name: str,
        node_label: str,
    ) -> list[ConsoleLogOutput]:
        """
        Get console output history by lab name and node label. Node must be started.
        Returns list of log entries with time (ms since start) and message. Includes all console ports.
        Useful for troubleshooting, monitoring boot progress, and verifying CLI command results.
        """
        client = get_cml_client_dep()
        try:
            lid = await resolve_lab_id(lab_name, client)
            nid = await resolve_node_id(lid, node_label, lab_name, client)
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Error resolving '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)
        return_lines = []
        for i in range(0, 2):  # Assume a maximum of 2 consoles per node
            try:
                resp = await client.get(f"/labs/{lid}/nodes/{nid}/consoles/{i}/log")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    continue  # Console index does not exist, try the next one
                else:
                    raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
            except Exception as e:
                logger.error(f"Error getting console log for node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
                raise ToolError(e)
            lines = re.split(r"\r?\n", resp)
            for line in lines:
                if not line.startswith("|"):
                    if len(return_lines) > 0:
                        return_lines[-1].message += "\n" + line
                    continue
                _, log_time, msg = line.split("|", 2)
                return_lines.append(ConsoleLogOutput(time=int(log_time), message=msg))

        return return_lines

    @mcp.tool(
        annotations={"title": "Send CLI Command to CML Node", "readOnlyHint": False, "destructiveHint": True},
    )
    async def send_cli_command(
        lab_name: str,
        node_label: str,
        commands: str,
        config_command: bool = False,
    ) -> str:
        """
        Send CLI commands to running node by lab name and node label. Node must be in BOOTED state.
        CRITICAL: Can modify device state. Review commands before executing, especially with config_command=true.
        Separate multiple commands with newlines.
        config_command=false (default): exec/operational mode. config_command=true: config mode (omit "configure terminal"/"end").
        Returns command output text.
        """
        client = get_cml_client_dep()

        if client.vclient is None:
            raise ToolError(
                "PyATS CLI commands require the virl2_client library. Ensure the CML client was initialized with valid credentials."
            )

        try:
            lid = await resolve_lab_id(lab_name, client)
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Error resolving lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)

        try:
            output = await asyncio.to_thread(_send_cli_command_sync, client, lid, node_label, commands, config_command)
            return output
        except Exception as e:
            logger.error(f"Error sending CLI command '{commands}' to node '{node_label}' in lab '{lab_name}': {str(e)}", exc_info=True)
            raise ToolError(e)
