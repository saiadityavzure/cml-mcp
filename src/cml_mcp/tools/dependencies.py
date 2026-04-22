# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.

# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.

"""
Dependency injection module for CML client management.
"""

import ast
import contextvars
import json
import logging
import unicodedata
from typing import Any, Optional

from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml_client import CMLClient
from cml_mcp.settings import settings

logger = logging.getLogger("cml-mcp.dependencies")

# Unicode characters that LLMs commonly substitute for ASCII hyphen-minus (U+002D)
_UNICODE_HYPHENS = str.maketrans(
    "‐‑‒–—―−﹘﹣－",
    "----------",
)


def _normalize_label(label: str) -> str:
    """Normalize a label for comparison: NFKC unicode normalization + ASCII hyphen substitution."""
    return unicodedata.normalize("NFKC", label).translate(_UNICODE_HYPHENS)

# Global singleton client for stdio transport
# Only initialize if we're using stdio transport to avoid resource waste
if settings.cml_mcp_transport == "stdio":
    cml_client = CMLClient(
        str(settings.cml_url),
        settings.cml_username,
        settings.cml_password,
        transport=str(settings.cml_mcp_transport),
        verify_ssl=settings.cml_verify_ssl,
    )
else:
    # In HTTP mode, we don't need a global client - each request creates its own
    cml_client = None  # type: ignore[assignment]

# Context variable to store request-scoped client for HTTP transport
_request_client: contextvars.ContextVar[Optional[CMLClient]] = contextvars.ContextVar("request_client", default=None)

# Context variables for PyATS credentials (per-request isolation)
_pyats_username: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("pyats_username", default=None)
_pyats_password: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("pyats_password", default=None)
_pyats_auth_pass: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("pyats_auth_pass", default=None)


def parse_str_arg(value: str) -> Any:
    """
    Parse a string argument that should be a dict/object.
    Handles both JSON strings (double quotes) and Python dict repr (single quotes).
    Raises ValueError if the string cannot be parsed as a dict.
    """
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    # Fallback: Python dict repr with single quotes (e.g. {'key': 'val'})
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError(f"Expected a dict, got {type(parsed).__name__}")
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"Cannot parse as object (tried JSON and Python dict repr): {e}") from e


async def cleanup_global_client() -> None:
    """Cleanup global CML client resources. Must be called before event loop shutdown."""
    if cml_client is not None and settings.cml_mcp_transport == "stdio":
        logger.info("Cleaning up global CML client...")
        try:
            await cml_client.close()
            logger.info("Successfully closed global CML client")
        except Exception as e:
            logger.error(f"Error closing global CML client: {e}", exc_info=True)
    else:
        logger.debug("No global CML client to clean up (HTTP mode or client is None)")


async def resolve_node_id(lid: UUID4Type, node_label: str, lab_name: str, client: CMLClient) -> UUID4Type:
    """Resolve a node label to its UUID within a lab. Raises ToolError if not found or ambiguous."""
    logger.info(f"Resolving node label '{node_label}' in lab '{lab_name}' ({lid})")
    nodes = await client.get(f"/labs/{lid}/nodes", params={"data": True, "operational": False, "exclude_configurations": True})
    needle = _normalize_label(node_label)
    matches = [n for n in list(nodes) if _normalize_label(n.get("label", "")) == needle]
    if not matches:
        logger.error(f"No node with label '{node_label}' found in lab '{lab_name}'")
        raise ToolError(f"No node found with label '{node_label}' in lab '{lab_name}'.")
    if len(matches) > 1:
        logger.error(f"Ambiguous node label '{node_label}' in lab '{lab_name}': {len(matches)} matches")
        raise ToolError(f"Multiple nodes found with label '{node_label}' in lab '{lab_name}'. Labels must be unique.")
    nid = UUID4Type(matches[0]["id"])
    logger.info(f"Resolved node '{node_label}' → {nid}")
    return nid


async def resolve_lab_id(lab_name: str, client: CMLClient) -> UUID4Type:
    """Resolve a lab title to its UUID. Raises ToolError if not found or ambiguous."""
    logger.info(f"Resolving lab name '{lab_name}' to UUID")
    labs = await client.get("/labs", params={"show_all": True})
    needle = _normalize_label(lab_name)
    matches = []
    for lid in labs:
        lab = await client.get(f"/labs/{lid}")
        if _normalize_label(lab.get("lab_title", "")) == needle:
            matches.append(UUID4Type(lid))
            logger.debug(f"Lab name match: '{lab_name}' → {lid}")
    if not matches:
        logger.error(f"No lab found with name '{lab_name}'")
        raise ToolError(f"No lab found with name '{lab_name}'.")
    if len(matches) > 1:
        logger.error(f"Ambiguous lab name '{lab_name}': found {len(matches)} matches")
        raise ToolError(f"Multiple labs found with name '{lab_name}'. Lab names must be unique.")
    logger.info(f"Resolved lab '{lab_name}' → {matches[0]}")
    return matches[0]


def get_cml_client_dep() -> CMLClient:
    """
    Dependency function to get the appropriate CML client.
    For HTTP transport, returns the request-scoped client.
    For stdio transport, returns the global singleton.
    """
    if settings.cml_mcp_transport == "http":
        client = _request_client.get()
        if client is None:
            raise RuntimeError(
                "No request client available in contextvar. This usually means the tool "
                "was called outside of a proper HTTP request context (e.g., from a spawned "
                "task without context propagation, or before middleware initialization). "
                "Check that async tasks properly inherit context."
            )
        return client
    else:
        if cml_client is None:
            raise RuntimeError("Global CML client is not initialized. This should never happen in stdio mode.")
        return cml_client
