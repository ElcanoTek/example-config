"""Pytest configuration for the example MCP server tests.

Puts the parent ``mcp/`` directory on ``sys.path`` so the test modules can
``import knowledge_base`` and ``import example_api`` directly, regardless of
where pytest is invoked from.
"""

import sys
from pathlib import Path

# tests/ lives inside mcp/, so the server modules are one directory up.
MCP_DIR = Path(__file__).resolve().parent.parent
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))
