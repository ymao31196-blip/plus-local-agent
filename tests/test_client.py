import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PROJECT_ROOT / "server.py"
EXPECTED_CONTENT = 'print("Agent execution successful.")'


def text_from_result(result: object) -> str:
    """Collect the text blocks returned by an MCP tool call."""
    blocks = getattr(result, "content", [])
    return "\n".join(
        block.text
        for block in blocks
        if getattr(block, "type", None) == "text"
    )


async def main() -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("MCP session initialized.")

            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]
            print("Available tools:")
            for tool_name in tool_names:
                print(f"- {tool_name}")

            for required_tool in ("write_text", "read_text"):
                if required_tool not in tool_names:
                    raise AssertionError(f"Missing required tool: {required_tool}")

            write_result = await session.call_tool(
                "write_text",
                {
                    "path": "hello.py",
                    "content": EXPECTED_CONTENT,
                },
            )
            if write_result.is_error:
                raise AssertionError(
                    f"write_text failed: {text_from_result(write_result)}"
                )
            print(f"write_text succeeded: {text_from_result(write_result)}")

            read_result = await session.call_tool(
                "read_text",
                {"path": "hello.py"},
            )
            if read_result.is_error:
                raise AssertionError(
                    f"read_text failed: {text_from_result(read_result)}"
                )

            returned_text = text_from_result(read_result)
            if EXPECTED_CONTENT not in returned_text:
                raise AssertionError(
                    "read_text returned unexpected content: "
                    f"{returned_text!r}"
                )

            print("read_text returned:")
            print(returned_text)
            print("Verification passed: MCP Client -> FastMCP Server -> local file I/O")


if __name__ == "__main__":
    asyncio.run(main())
