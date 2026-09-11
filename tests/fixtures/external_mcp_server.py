from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("PLA v0.13 external fixture")


@mcp.tool(tags={"demo", "text"})
def echo_text(text: str, repeat: int = 1) -> dict:
    """Echo text a bounded number of times."""
    if repeat < 1 or repeat > 5:
        raise ValueError("repeat must be between 1 and 5")
    return {"echo": text * repeat, "repeat": repeat}


@mcp.tool(tags={"math"})
def add_numbers(a: int, b: int) -> dict:
    """Add two integers."""
    return {"sum": a + b}


@mcp.tool(
    tags={"artifact", "file"},
    meta={
        "pla_artifacts": {
            "inputs": ["input_artifact"],
            "outputs": ["output_path"],
        }
    },
)
def uppercase_file(input_artifact: str) -> dict:
    """Uppercase one UTF-8 text artifact and return a local output path."""
    source = Path(input_artifact)
    output = source.with_name(source.name + ".upper.txt")
    output.write_text(source.read_text(encoding="utf-8").upper(), encoding="utf-8")
    return {
        "input_artifact": str(source.resolve()),
        "output_path": str(output.resolve()),
    }


@mcp.tool(
    tags={"artifact", "security-test"},
    meta={"pla_artifacts": {"inputs": [], "outputs": ["output_path"]}},
)
def escape_output() -> dict:
    """Return a file path outside the invocation workspace for boundary testing."""
    return {"output_path": str(Path(__file__).resolve())}


@mcp.tool(tags={"artifact", "managed-output"})
def managed_copy(input_artifact: str, output_path: str) -> dict:
    """Copy a UTF-8 input artifact to a caller-managed output path."""
    source = Path(input_artifact)
    destination = Path(output_path)
    destination.write_text(
        source.read_text(encoding="utf-8") + "\nmanaged",
        encoding="utf-8",
    )
    return {
        "result": f"saved {destination.resolve()}",
    }


if __name__ == "__main__":
    mcp.run()
