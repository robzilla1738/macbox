"""macbox power MCP server profile.

This entrypoint exposes only advanced guest-control tools. Configure it next to
macbox_core_mcp.py when an agent needs UI automation, scripts, installers,
release gates, warm VMs, or arbitrary guest file transfer.
"""

from __future__ import annotations

import os

os.environ.setdefault("MACBOX_MCP_PROFILE", "power")

import macbox_mcp

main = macbox_mcp.main


if __name__ == "__main__":
    main()
