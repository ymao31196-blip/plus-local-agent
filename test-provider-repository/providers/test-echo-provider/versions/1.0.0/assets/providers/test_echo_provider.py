"""Test Echo Provider for PLA Component Management Testing"""

from mcp.server.mcpserver import MCPServer
from typing import Dict, Any

mcp = MCPServer("Test Echo Provider")


@mcp.tool()
def echo_message(message: str) -> str:
    """Echo a message back to the caller.

    Args:
        message: The message to echo

    Returns:
        The echoed message with a prefix
    """
    return f"Echo: {message}"


@mcp.tool()
def echo_structured(data: Dict[str, Any]) -> Dict[str, Any]:
    """Echo structured data back to the caller.

    Args:
        data: A dictionary of data to echo

    Returns:
        The data wrapped in an echo structure
    """
    return {
        "echo": data,
        "provider": "test-echo-provider",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    mcp.run()
