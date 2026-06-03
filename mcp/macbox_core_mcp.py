"""macbox core MCP server profile.

This entrypoint exposes the low-token core tool set for normal app smoke tests.
Use macbox_mcp.py directly for the backward-compatible full surface.
"""

from __future__ import annotations

import os

os.environ.setdefault("MACBOX_MCP_PROFILE", "core")

import macbox_mcp

main = macbox_mcp.main


if __name__ == "__main__":
    main()
