"""MCP adapter — sibling of `app.web`. Thin tool wrappers over `app.services`.

Surfaced over Streamable HTTP at `/mcp/` (mounted in `app.main`)."""
from .server import mcp

__all__ = ["mcp"]
