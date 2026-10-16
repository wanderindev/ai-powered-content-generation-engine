"""MCP tool implementations (#640 onward), registered by ``app.mcp.server``.

Each module holds plain functions plus their agent-facing descriptions; the
server wraps them with ``pic_tool`` (scope check + audit), which stays the only
registration path.
"""
