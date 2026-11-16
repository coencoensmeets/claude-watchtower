#!/usr/bin/env python3
"""The panel as an MCP server for Claude Code. See watchtower/mcp.py.

    claude mcp add --scope user watchtower -- python3 /path/to/claude-watchtower/mcp_server.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from watchtower.mcp import main  # noqa: E402

main()
